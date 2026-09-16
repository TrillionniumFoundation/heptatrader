#!/usr/bin/env python3
"""Canonical OMS lifecycle facade.

The reviewed V2 implementation lives in ``hepta_oms_lifecycle_core``.  This
facade keeps that implementation stable while adding two narrow behaviours:

* verification of cumulative runtime/send indexes is streaming rather than
  materializing the whole historical index; and
* every newly sealed V2 generation carries a digest-bound simulator recovery
  projection in ``checkpoint.json`` before the lineage pointer is published.

The projection is deliberately bounded by open simulator orders and non-zero
positions.  Historical command identity remains in the normal generation
indexes; this file does not create a second command ledger or grant PAPER/LIVE
authority.
"""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
from typing import Any, Callable, Iterator

import hepta_oms_lifecycle_core as _core

# Re-export the reviewed implementation, including private helpers used by the
# repository's focused tests.  Only the functions replaced below diverge.
for _name in dir(_core):
    if _name not in {"verify_generation", "seal_generation", "main"}:
        globals()[_name] = getattr(_core, _name)

_core_verify_generation = _core.verify_generation
_core_seal_generation = _core.seal_generation

_SIM_HEADER = "HEPTA_SIMULATOR_RECOVERY_V1"
_SIM_FIELD = "simulator_recovery_hex"
_SIM_SCHEMA_FIELD = "simulator_recovery_schema"
_SIM_MAX_PAYLOAD = 8 * 1024 * 1024


def _new_simulator_state() -> dict[str, Any]:
    return {
        "admitted_order_count": 0,
        "next_order_id": 1_000_000,
        "positions": {},
        "active_orders": {},
    }


def _finite_positive(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and \
        math.isfinite(float(value)) and float(value) > 0.0


def _apply_simulator_event(state: dict[str, Any], event: dict[str, Any]) -> None:
    order_id = event.get("order_id", -1)
    if not isinstance(order_id, int) or isinstance(order_id, bool):
        order_id = -1
    prior_watermark = int(state["next_order_id"]) - 1
    kind = event.get("event", "")
    fill = kind == "status" and event.get("status", "") == "Filled"
    terminal = kind == "status" and event.get("status", "") in {
        "Cancelled", "ApiCancelled", "Inactive", "Rejected"
    }

    if kind == "place_sent" or fill:
        instrument = event.get("instrument", "")
        side = event.get("side", "")
        qty = event.get("qty", 0.0)
        if (order_id < 0 or event.get("venue", "") != "SIMULATOR" or
                event.get("account", "") != "SIM" or not isinstance(instrument, str) or
                not instrument or side not in {"BUY", "SELL"} or not _finite_positive(qty)):
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
                # Simulator order ids are monotonically allocated.  Reusing an
                # id at or below the sealed watermark means a terminal order was
                # replayed or identity was lost; fail closed rather than count it
                # twice.
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
    elif terminal and order_id >= 0:
        state["active_orders"].pop(order_id, None)

    if order_id >= 0:
        if order_id >= (1 << 63) - 1:
            raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_ORDER_ID_EXHAUSTED")
        state["next_order_id"] = max(int(state["next_order_id"]), order_id + 1)


def _iter_segment_events(path: Path, expected_records: int) -> Iterator[dict[str, Any]]:
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


def _encode_simulator_state(state: dict[str, Any]) -> bytes:
    positions = state["positions"]
    active = state["active_orders"]
    lines = [
        _SIM_HEADER,
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
    if len(payload) > _SIM_MAX_PAYLOAD:
        raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_STATE_LIMIT")
    return payload


def _decode_simulator_state(payload: bytes) -> dict[str, Any]:
    if len(payload) > _SIM_MAX_PAYLOAD:
        raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_STATE_LIMIT")
    try:
        lines = payload.decode("ascii").splitlines()
    except UnicodeError as error:
        raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_STATE_INVALID") from error
    if len(lines) < 5 or lines[0] != _SIM_HEADER:
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
    state = _new_simulator_state()
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


def _checkpoint_simulator_state(root: Path) -> dict[str, Any] | None:
    checkpoint = v1._load_json_private(root / "checkpoint.json")
    encoded = checkpoint.get(_SIM_FIELD) if isinstance(checkpoint, dict) else None
    schema = checkpoint.get(_SIM_SCHEMA_FIELD) if isinstance(checkpoint, dict) else None
    if encoded is None and schema is None:
        return None
    if schema != _SIM_HEADER or not isinstance(encoded, str) or len(encoded) % 2:
        raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_CHECKPOINT_INVALID")
    try:
        payload = bytes.fromhex(encoded)
    except ValueError as error:
        raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_CHECKPOINT_INVALID") from error
    return _decode_simulator_state(payload)


def _rebuild_parent_simulator_state(store: Path, generation: str) -> dict[str, Any]:
    state = _new_simulator_state()
    chain = _core._generation_chain(store, generation)
    base_index = 0
    for index, (_name, manifest) in enumerate(chain):
        if manifest.get("schema") == v1.SCHEMA:
            base_index = index
    for name, manifest in chain[base_index:]:
        if manifest.get("schema") == v1.SCHEMA:
            expected = int(manifest.get("journal_records", 0))
        elif manifest.get("schema") == _core.SCHEMA:
            expected = int(manifest.get("segment_records", 0))
        else:
            raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_PARENT_SCHEMA_UNSUPPORTED")
        for event in _iter_segment_events(store / name / "segment-000001.jsonl", expected):
            _apply_simulator_event(state, event)
    return state


def _augment_generation_simulator_recovery(store: Path, root: Path) -> None:
    manifest = v1._load_json_private(root / "manifest.json")
    if not isinstance(manifest, dict) or manifest.get("schema") != _core.SCHEMA:
        raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_GENERATION_INVALID")
    parent = manifest.get("parent_generation") or ""
    state = None
    if parent:
        state = _checkpoint_simulator_state(store / parent)
        if state is None:
            state = _rebuild_parent_simulator_state(store, parent)
    if state is None:
        state = _new_simulator_state()
    expected = int(manifest.get("segment_records", 0))
    for event in _iter_segment_events(root / "segment-000001.jsonl", expected):
        _apply_simulator_event(state, event)

    payload = _encode_simulator_state(state)
    checkpoint = v1._load_json_private(root / "checkpoint.json")
    if not isinstance(checkpoint, dict):
        raise v1.GenerationError("OMS_GENERATION_CHECKPOINT_MISMATCH")
    checkpoint[_SIM_SCHEMA_FIELD] = _SIM_HEADER
    checkpoint[_SIM_FIELD] = payload.hex()
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


def seal_generation(journal: Path, store: Path, *, stopped: bool,
                    max_bytes: int = 64 * 1024 * 1024,
                    max_records: int = 65536,
                    max_record_bytes: int = 262144,
                    phase_hook: Callable[[str], None] | None = None) -> dict[str, Any]:
    existing = {entry.name for entry in store.iterdir() if entry.is_dir()} if store.exists() else set()
    user_hook = phase_hook or (lambda _phase: None)

    def hook(phase: str) -> None:
        if phase == "generation-durable":
            created = [entry for entry in store.iterdir()
                       if entry.is_dir() and entry.name not in existing and
                       (entry / "manifest.json").exists()]
            if len(created) != 1:
                raise v1.GenerationError("OMS_SIMULATOR_RECOVERY_GENERATION_AMBIGUOUS")
            _augment_generation_simulator_recovery(store, created[0])
        user_hook(phase)

    return _core_seal_generation(
        journal, store, stopped=stopped, max_bytes=max_bytes,
        max_records=max_records, max_record_bytes=max_record_bytes,
        phase_hook=hook)


def _stream_runtime_index(path: Path, expected: int) -> None:
    count = 0
    previous = None
    for line in _core._iter_private_lines(path):
        key, _record, _fields = _core._runtime_row(line)
        if previous is not None and key <= previous:
            raise v1.GenerationError("OMS_GENERATION_RUNTIME_INDEX_ORDER_INVALID")
        previous = key
        count += 1
    if count != expected:
        raise v1.GenerationError("OMS_GENERATION_RUNTIME_INDEX_COUNT_MISMATCH")


def _stream_send_index(path: Path, expected: int, sorted_send_index: bool) -> None:
    count = 0
    previous = None
    for line in _core._iter_private_lines(path):
        count += 1
        if not sorted_send_index:
            continue
        try:
            fields = line.rstrip(b"\n").decode("ascii").split("\t")
            if len(fields) != 7:
                raise ValueError("wrong field count")
            key = (fields[0], fields[1], int(fields[2]), int(fields[6]),
                   fields[3], fields[4], fields[5])
        except (UnicodeError, ValueError) as error:
            raise v1.GenerationError("OMS_GENERATION_SEND_INDEX_INVALID") from error
        if previous is not None and key <= previous:
            raise v1.GenerationError("OMS_GENERATION_SEND_INDEX_ORDER_INVALID")
        previous = key
    if count != expected:
        raise v1.GenerationError("OMS_GENERATION_SEND_INDEX_COUNT_MISMATCH")


def verify_generation(store: Path, generation: str | None = None,
                      journal: Path | None = None) -> dict[str, Any]:
    if not v1._private_directory(os.stat(store, follow_symlinks=False)):
        raise v1.GenerationError("OMS_GENERATION_PRIVATE_STORE_REQUIRED")
    current = v1._read_current(store)
    if generation is None:
        if current is None:
            raise v1.GenerationError("OMS_GENERATION_CURRENT_MISSING")
        generation = current["generation"]
    manifest = _core._manifest_for(store, generation)
    if manifest.get("schema") == v1.SCHEMA:
        result = v1.verify_generation(store, generation)
        return {**result, "schema": v1.SCHEMA,
                "history_records": manifest["journal_records"]}
    if manifest.get("schema") != _core.SCHEMA:
        raise v1.GenerationError("OMS_GENERATION_MANIFEST_INVALID")
    root = store / generation
    if not v1._private_directory(os.stat(root, follow_symlinks=False)):
        raise v1.GenerationError("OMS_GENERATION_DIRECTORY_INVALID")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise v1.GenerationError("OMS_GENERATION_FILE_INVENTORY_INVALID")
    for name, expected_file in files.items():
        if not isinstance(name, str) or "/" in name or not isinstance(expected_file, dict):
            raise v1.GenerationError("OMS_GENERATION_FILE_INVENTORY_INVALID")
        size, digest = v1._sha256_file(root / name)
        if size != expected_file.get("bytes") or digest != expected_file.get("sha256"):
            raise v1.GenerationError("OMS_GENERATION_DIGEST_MISMATCH")

    runtime_raw = v1._read_private_bytes(root / "runtime-manifest.txt")
    runtime = v1._parse_line_manifest(runtime_raw, _core.RUNTIME_MANIFEST_HEADER)
    required_legacy = {
        "generation", "parent_generation", "parent_manifest_sha256", "history_records",
        "segment_records", "command_records", "send_attempt_records", "hot_replay_records",
        "segment_sha256", "checkpoint_sha256", "command_index_sha256",
        "runtime_command_index_sha256", "send_attempt_index_sha256", "hot_replay_sha256",
        "active_tail_header_bytes", "active_tail_header_sha256", "authorization_effect",
        "paper_authorized", "live_authorized",
    }
    required_sorted = set(required_legacy)
    required_sorted.add("send_attempt_index_order")
    sorted_send_index = set(runtime) == required_sorted
    if (set(runtime) not in {frozenset(required_legacy), frozenset(required_sorted)} or
            (sorted_send_index and runtime["send_attempt_index_order"] != "account-domain-time-v1") or
            runtime["generation"] != generation or runtime["authorization_effect"] != "NONE" or
            runtime["paper_authorized"] != "0" or runtime["live_authorized"] != "0"):
        raise v1.GenerationError("OMS_GENERATION_RUNTIME_MANIFEST_INVALID")
    marker = _core._tail_header(generation)
    expected_counts = {
        "history_records": manifest.get("history_records"),
        "segment_records": manifest.get("segment_records"),
        "command_records": manifest.get("command_records"),
        "send_attempt_records": manifest.get("send_attempt_records"),
        "hot_replay_records": manifest.get("hot_replay_records"),
    }
    for name, value in expected_counts.items():
        if type(value) is not int or runtime[name] != str(value):
            raise v1.GenerationError("OMS_GENERATION_RUNTIME_MANIFEST_MISMATCH")
    parent_generation = manifest.get("parent_generation") or ""
    parent_manifest_sha256 = manifest.get("parent_manifest_sha256") or ""
    if (runtime["parent_generation"] != (parent_generation or "-") or
            runtime["parent_manifest_sha256"] != (parent_manifest_sha256 if parent_generation else "-") or
            runtime["active_tail_header_bytes"] != str(len(marker)) or
            runtime["active_tail_header_sha256"] != hashlib.sha256(marker).hexdigest()):
        raise v1.GenerationError("OMS_GENERATION_RUNTIME_MANIFEST_MISMATCH")
    digest_fields = {
        "segment_sha256": "segment-000001.jsonl", "checkpoint_sha256": "checkpoint.json",
        "command_index_sha256": "command-index.tsv",
        "runtime_command_index_sha256": "runtime-command-index.tsv",
        "send_attempt_index_sha256": "send-attempt-index.tsv",
        "hot_replay_sha256": "hot-replay.jsonl",
    }
    for field, name in digest_fields.items():
        if runtime[field] != files[name]["sha256"]:
            raise v1.GenerationError("OMS_GENERATION_RUNTIME_MANIFEST_MISMATCH")
    if hashlib.sha256(runtime_raw).hexdigest() != manifest.get("runtime_manifest_sha256"):
        raise v1.GenerationError("OMS_GENERATION_RUNTIME_MANIFEST_MISMATCH")

    # These were the two O(history) list materializations in the reviewed V2
    # verifier.  Validate count/order in one pass and retain only one prior key.
    _stream_runtime_index(root / "runtime-command-index.tsv", manifest["command_records"])
    _stream_send_index(root / "send-attempt-index.tsv",
                       manifest["send_attempt_records"], sorted_send_index)

    simulator_state = _checkpoint_simulator_state(root)
    if simulator_state is not None:
        # Re-encoding catches non-canonical or internally inconsistent payloads
        # while keeping verification memory proportional to active simulator
        # state, not command history.
        _encode_simulator_state(simulator_state)

    if current and current["generation"] == generation:
        manifest_digest = v1._sha256_file(root / "manifest.json")[1]
        _core._verify_runtime_current(store, generation, manifest_digest,
                                      manifest["runtime_manifest_sha256"])
        if journal is not None:
            fd = os.open(journal, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                info = os.fstat(fd)
                parsed = _core._parse_tail_header(fd, info.st_size)
                if parsed is None or parsed[0] != generation or parsed[1] != len(marker):
                    raise v1.GenerationError("OMS_ACTIVE_TAIL_LINEAGE_MISMATCH")
            finally:
                os.close(fd)
    parent = manifest.get("parent_generation") or ""
    if parent:
        parent_manifest = _core._manifest_for(store, parent)
        if (parent_manifest.get("generation") != parent or
                v1._sha256_file(store / parent / "manifest.json")[1] != parent_manifest_sha256):
            raise v1.GenerationError("OMS_GENERATION_PARENT_INVALID")
    return {
        "schema": _core.SCHEMA, "result": "PASS", "generation": generation,
        "history_records": manifest["history_records"],
        "segment_records": manifest["segment_records"],
        "command_records": manifest["command_records"],
        "send_attempt_records": manifest["send_attempt_records"],
        "hot_replay_records": manifest["hot_replay_records"],
        "simulator_recovery": simulator_state is not None,
        "authorization_effect": "NONE",
    }


# Internal core paths (parent verification and CLI dispatch) resolve these names
# dynamically.  Patch only the two reviewed extension points, not the rest of
# the implementation.
_core.verify_generation = verify_generation
_core.seal_generation = seal_generation


def main(argv: list[str] | None = None) -> int:
    return _core.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
