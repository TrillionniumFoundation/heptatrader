#!/usr/bin/env python3
"""Seal OMS history into lineage generations and rotate a fail-closed active tail.

This is the stopped-state long-horizon companion to hepta_oms_checkpoint.py.
It never expires command identity and never grants PAPER/LIVE authority.

A v2 generation stores only the newly sealed JSONL segment, but publishes a
cumulative full-key command index, cumulative send-attempt index, and bounded
hot replay. The active journal is replaced by a lineage sentinel followed only
by new JSONL events. Legacy full-journal replay rejects that sentinel, while
generation-aware native recovery verifies it and replays bytes after it.
`export` reconstructs an ordinary complete JSONL journal for explicit downgrade.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
import uuid
from typing import Any, Callable, Iterable, Iterator

import hepta_oms_checkpoint as v1
from verify_oms_journal_replay import (
    JournalError,
    reject_constant,
    unique_object,
    validate_event,
    validated_raw_records,
)

SCHEMA = "heptatrader.oms-generation.v2"
RUNTIME_MANIFEST_HEADER = "HEPTA_OMS_RUNTIME_GENERATION_V2"
TAIL_HEADER = "HEPTA_OMS_ACTIVE_TAIL_V1"
SEND_INDEX_ORDER = "account-domain-time-v1"
MAX_METADATA = 16 * 1024 * 1024
SEND_SORT_CHUNK_ROWS = 8192


def _tail_header(generation: str) -> bytes:
    if not generation or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for c in generation):
        raise v1.GenerationError("OMS_GENERATION_NAME_INVALID")
    return f"{TAIL_HEADER}\t{generation}\n".encode("ascii")


def _parse_tail_header(fd: int, size: int) -> tuple[str, int] | None:
    maximum = 512
    raw = os.pread(fd, min(size, maximum), 0)
    newline = raw.find(b"\n")
    if newline < 0:
        if size >= maximum:
            raise v1.GenerationError("OMS_ACTIVE_TAIL_HEADER_LIMIT")
        return None
    line = raw[:newline]
    prefix = (TAIL_HEADER + "\t").encode("ascii")
    if not line.startswith(prefix):
        return None
    try:
        generation = line[len(prefix):].decode("ascii")
    except UnicodeError as error:
        raise v1.GenerationError("OMS_ACTIVE_TAIL_HEADER_INVALID") from error
    expected = _tail_header(generation)
    if raw[:len(expected)] != expected:
        raise v1.GenerationError("OMS_ACTIVE_TAIL_HEADER_INVALID")
    return generation, len(expected)


def _strict_records(fd: int, start: int, end: int, *, max_bytes: int,
                    max_records: int, max_record_bytes: int) -> tuple[list[bytes], list[dict[str, Any]]]:
    if start < 0 or end < start or end - start > max_bytes:
        raise v1.GenerationError("OMS_REPLAY_BYTE_LIMIT")
    raw_records: list[bytes] = []
    events: list[dict[str, Any]] = []
    pending = bytearray()
    offset = start
    while offset < end:
        chunk = os.pread(fd, min(1024 * 1024, end - offset), offset)
        if not chunk:
            raise v1.GenerationError("OMS_REPLAY_IO_FAILURE")
        offset += len(chunk)
        cursor = 0
        while cursor < len(chunk):
            newline = chunk.find(b"\n", cursor)
            fragment = chunk[cursor:newline if newline >= 0 else len(chunk)]
            if len(fragment) > max_record_bytes - len(pending):
                raise v1.GenerationError("OMS_REPLAY_RECORD_BYTE_LIMIT")
            pending.extend(fragment)
            if newline < 0:
                break
            if not pending:
                raise v1.GenerationError("OMS_REPLAY_EMPTY_RECORD")
            if len(events) >= max_records:
                raise v1.GenerationError("OMS_REPLAY_RECORD_COUNT_LIMIT")
            raw = bytes(pending)
            try:
                value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object,
                                   parse_constant=reject_constant)
                value = validate_event(value, len(events) + 1)
            except (JournalError, json.JSONDecodeError, UnicodeError, RecursionError) as error:
                raise v1.GenerationError("OMS_REPLAY_INVALID_RECORD") from error
            value.pop("_line", None)
            raw_records.append(raw + b"\n")
            events.append(value)
            pending.clear()
            cursor = newline + 1
    if pending:
        raise v1.GenerationError("OMS_REPLAY_TORN_RECORD")
    return raw_records, events


def _read_hot(generation_dir: Path, maximum_bytes: int, maximum_records: int,
              maximum_record_bytes: int) -> list[dict[str, Any]]:
    path = generation_dir / "hot-replay.jsonl"
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not v1._private_regular(info):
            raise v1.GenerationError("OMS_GENERATION_UNSAFE_HOT_REPLAY")
        _, events = _strict_records(fd, 0, info.st_size, max_bytes=maximum_bytes,
                                    max_records=maximum_records,
                                    max_record_bytes=maximum_record_bytes)
        return events
    finally:
        os.close(fd)


def _runtime_row(line: bytes) -> tuple[tuple[str, str, str], dict[str, Any], list[str]]:
    if len(line) > v1.MAX_INDEX_LINE:
        raise v1.GenerationError("OMS_GENERATION_RUNTIME_INDEX_LINE_LIMIT")
    try:
        fields = line.rstrip(b"\n").decode("ascii").split("\t")
    except UnicodeError as error:
        raise v1.GenerationError("OMS_GENERATION_RUNTIME_INDEX_RECORD_INVALID") from error
    if len(fields) != 13 or fields[12] not in {"0", "1"}:
        raise v1.GenerationError("OMS_GENERATION_RUNTIME_INDEX_RECORD_INVALID")
    try:
        record = {
            "agent_id": v1._unhex(fields[0]), "session_id": v1._unhex(fields[1]),
            "command_id": v1._unhex(fields[2]), "request_hash": v1._unhex(fields[3]),
            "operation": fields[4], "status": fields[5], "order_id": int(fields[6]),
            "reason": v1._unhex(fields[7]), "venue_correlation_id": v1._unhex(fields[8]),
            "last_sequence": int(fields[9]), "account": v1._unhex(fields[10]),
            "execution_domain": v1._unhex(fields[11]),
            "durable_mutation_intent": fields[12] == "1",
        }
    except (ValueError, OverflowError) as error:
        raise v1.GenerationError("OMS_GENERATION_RUNTIME_INDEX_RECORD_INVALID") from error
    if record["operation"] not in {"place", "cancel", "flatten"} or record["status"] not in {"accepted", "rejected", "uncertain"}:
        raise v1.GenerationError("OMS_GENERATION_RUNTIME_INDEX_RECORD_INVALID")
    return (fields[0], fields[1], fields[2]), record, fields


def _iter_private_lines(path: Path) -> Iterator[bytes]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if not v1._private_regular(before):
            raise v1.GenerationError("OMS_GENERATION_UNSAFE_INDEX")
        pending = bytearray()
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            pending.extend(block)
            while True:
                newline = pending.find(b"\n")
                if newline < 0:
                    break
                yield bytes(pending[:newline + 1])
                del pending[:newline + 1]
        if pending:
            raise v1.GenerationError("OMS_GENERATION_INDEX_TORN_RECORD")
        after = os.fstat(fd)
        named = os.stat(path, follow_symlinks=False)
        if v1._identity(before) != v1._identity(after) or v1._identity(after) != v1._identity(named):
            raise v1.GenerationError("OMS_GENERATION_INDEX_CHANGED")
    finally:
        os.close(fd)


def _manifest_for(store: Path, generation: str) -> dict[str, Any]:
    root = store / generation
    manifest = v1._load_json_private(root / "manifest.json")
    if not isinstance(manifest, dict) or manifest.get("generation") != generation:
        raise v1.GenerationError("OMS_GENERATION_MANIFEST_INVALID")
    return manifest


def _parent_state(store: Path, current: dict[str, Any] | None, journal_fd: int,
                  journal_size: int, max_bytes: int, max_records: int,
                  max_record_bytes: int) -> tuple[str, int, int, list[dict[str, Any]], Path | None, int]:
    if current is None:
        return "", 0, 0, [], None, 0
    generation = current["generation"]
    manifest = _manifest_for(store, generation)
    root = store / generation
    schema = manifest.get("schema")
    if schema == v1.SCHEMA:
        v1.verify_generation(store, generation)
        prefix = manifest.get("journal_prefix_bytes")
        history_records = manifest.get("journal_records")
        if type(prefix) is not int or type(history_records) is not int or not 0 <= prefix <= journal_size:
            raise v1.GenerationError("OMS_GENERATION_PARENT_RANGE_INVALID")
        digest = hashlib.sha256()
        offset = 0
        while offset < prefix:
            block = os.pread(journal_fd, min(1024 * 1024, prefix - offset), offset)
            if not block:
                raise v1.GenerationError("OMS_GENERATION_PARENT_PREFIX_IO_FAILURE")
            digest.update(block)
            offset += len(block)
        if digest.hexdigest() != manifest.get("journal_logical_sha256"):
            raise v1.GenerationError("OMS_GENERATION_PARENT_PREFIX_MISMATCH")
        hot = _read_hot(root, max_bytes, max_records, max_record_bytes)
        return generation, prefix, history_records, hot, root, 0
    if schema != SCHEMA:
        raise v1.GenerationError("OMS_GENERATION_PARENT_SCHEMA_UNSUPPORTED")
    verified = verify_generation(store, generation=generation)
    history_records = verified["history_records"]
    parsed = _parse_tail_header(journal_fd, journal_size)
    if parsed is None or parsed[0] != generation:
        raise v1.GenerationError("OMS_ACTIVE_TAIL_LINEAGE_MISMATCH")
    hot = _read_hot(root, max_bytes, max_records, max_record_bytes)
    return generation, parsed[1], history_records, hot, root, parsed[1]


def _merge_command_record(parent: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    merged = dict(parent)
    for key in ("request_hash", "operation", "reason", "venue_correlation_id",
                "account", "execution_domain"):
        if update.get(key):
            merged[key] = update[key]
    if update.get("status") and update.get("status") != "unknown":
        merged["status"] = update["status"]
    if int(update.get("order_id", -1)) >= 0:
        merged["order_id"] = int(update["order_id"])
    merged["last_sequence"] = max(int(parent.get("last_sequence", 0)),
                                  int(update.get("last_sequence", 0)))
    merged["durable_mutation_intent"] = bool(
        parent.get("durable_mutation_intent") or update.get("durable_mutation_intent"))
    return merged


def _send_key(line: bytes) -> tuple[str, str, int, int, str, str, str]:
    if len(line) > v1.MAX_INDEX_LINE:
        raise v1.GenerationError("OMS_GENERATION_SEND_INDEX_INVALID")
    try:
        fields = line.rstrip(b"\n").decode("ascii").split("\t")
    except UnicodeError as error:
        raise v1.GenerationError("OMS_GENERATION_SEND_INDEX_INVALID") from error
    if len(fields) != 7:
        raise v1.GenerationError("OMS_GENERATION_SEND_INDEX_INVALID")
    try:
        timestamp = int(fields[2])
        sequence = int(fields[6])
        v1._unhex(fields[0]); v1._unhex(fields[1])
        v1._unhex(fields[3]); v1._unhex(fields[4]); v1._unhex(fields[5])
    except (ValueError, OverflowError, v1.GenerationError) as error:
        raise v1.GenerationError("OMS_GENERATION_SEND_INDEX_INVALID") from error
    return (fields[0], fields[1], timestamp, sequence,
            fields[3], fields[4], fields[5])


def _parent_send_index_sorted(parent_dir: Path | None) -> bool:
    if parent_dir is None:
        return True
    manifest_path = parent_dir / "runtime-manifest.txt"
    try:
        raw = v1._read_private_bytes(manifest_path, MAX_METADATA)
        fields = v1._parse_line_manifest(raw, RUNTIME_MANIFEST_HEADER)
    except (OSError, ValueError, v1.GenerationError):
        return False
    return fields.get("send_attempt_index_order") == SEND_INDEX_ORDER


def _write_private_lines(path: Path, rows: Iterable[bytes]) -> int:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    count = 0
    try:
        previous = None
        for row in rows:
            key = _send_key(row)
            if previous is not None and key <= previous:
                raise v1.GenerationError("OMS_GENERATION_SEND_INDEX_ORDER_INVALID")
            v1._write_all(fd, row)
            previous = key
            count += 1
        v1._fsync(fd)
    finally:
        os.close(fd)
    return count


def _merge_sorted_send_files(left: Path, right: Path, output: Path) -> int:
    left_iter = iter(_iter_private_lines(left))
    right_iter = iter(_iter_private_lines(right))
    try:
        left_row = next(left_iter)
    except StopIteration:
        left_row = None
    try:
        right_row = next(right_iter)
    except StopIteration:
        right_row = None
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    count = 0
    previous = None
    try:
        while left_row is not None or right_row is not None:
            left_key = _send_key(left_row) if left_row is not None else None
            right_key = _send_key(right_row) if right_row is not None else None
            if left_key is not None and right_key is not None and left_key == right_key:
                raise v1.GenerationError("OMS_GENERATION_SEND_INDEX_DUPLICATE")
            take_left = left_row is not None and (right_key is None or left_key < right_key)
            row = left_row if take_left else right_row
            key = left_key if take_left else right_key
            if previous is not None and key <= previous:
                raise v1.GenerationError("OMS_GENERATION_SEND_INDEX_ORDER_INVALID")
            v1._write_all(fd, row)
            previous = key
            count += 1
            if take_left:
                try:
                    left_row = next(left_iter)
                except StopIteration:
                    left_row = None
            else:
                try:
                    right_row = next(right_iter)
                except StopIteration:
                    right_row = None
        v1._fsync(fd)
    finally:
        os.close(fd)
    return count


def _external_sort_send_index(generation_dir: Path, parent_dir: Path | None,
                              tail_lines: list[bytes], output: Path) -> int:
    temporary: list[Path] = []
    rows: list[bytes] = []
    count = 0

    def flush_chunk() -> None:
        nonlocal rows
        if not rows:
            return
        keyed = sorted((_send_key(row), row) for row in rows)
        for index in range(1, len(keyed)):
            if keyed[index - 1][0] == keyed[index][0]:
                raise v1.GenerationError("OMS_GENERATION_SEND_INDEX_DUPLICATE")
        path = generation_dir / f".send-sort-{uuid.uuid4().hex}.tmp"
        _write_private_lines(path, (row for _, row in keyed))
        temporary.append(path)
        rows = []

    try:
        if parent_dir is not None:
            for row in _iter_private_lines(parent_dir / "send-attempt-index.tsv"):
                _send_key(row)
                rows.append(row)
                count += 1
                if len(rows) >= SEND_SORT_CHUNK_ROWS:
                    flush_chunk()
        for row in tail_lines:
            _send_key(row)
            rows.append(row)
            count += 1
            if len(rows) >= SEND_SORT_CHUNK_ROWS:
                flush_chunk()
        flush_chunk()
        if not temporary:
            fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            try:
                v1._fsync(fd)
            finally:
                os.close(fd)
            return 0
        while len(temporary) > 1:
            merged: list[Path] = []
            for offset in range(0, len(temporary), 2):
                if offset + 1 == len(temporary):
                    merged.append(temporary[offset])
                    continue
                path = generation_dir / f".send-merge-{uuid.uuid4().hex}.tmp"
                _merge_sorted_send_files(temporary[offset], temporary[offset + 1], path)
                temporary[offset].unlink()
                temporary[offset + 1].unlink()
                merged.append(path)
            temporary = merged
        os.replace(temporary[0], output)
        temporary.clear()
        return count
    finally:
        for path in temporary:
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def _write_merged_send_index(generation_dir: Path, parent_dir: Path | None,
                             tail_attempts: list[dict[str, Any]],
                             history_base: int) -> int:
    tail_lines: list[bytes] = []
    for attempt in tail_attempts:
        copied = dict(attempt)
        copied["sequence"] = history_base + int(copied["sequence"])
        tail_lines.append(v1._send_attempt_line(copied))
    tail_lines.sort(key=_send_key)
    send_path = generation_dir / "send-attempt-index.tsv"

    if parent_dir is None or not _parent_send_index_sorted(parent_dir):
        return _external_sort_send_index(generation_dir, parent_dir, tail_lines, send_path)

    parent_iter = iter(_iter_private_lines(parent_dir / "send-attempt-index.tsv"))
    try:
        parent_row = next(parent_iter)
    except StopIteration:
        parent_row = None
    tail_index = 0
    previous_parent = None
    fd = os.open(send_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    count = 0
    try:
        while parent_row is not None or tail_index < len(tail_lines):
            parent_key = _send_key(parent_row) if parent_row is not None else None
            if parent_key is not None and previous_parent is not None and parent_key <= previous_parent:
                raise v1.GenerationError("OMS_GENERATION_PARENT_SEND_INDEX_ORDER_INVALID")
            tail_key = _send_key(tail_lines[tail_index]) if tail_index < len(tail_lines) else None
            if parent_key is not None and tail_key is not None and parent_key == tail_key:
                raise v1.GenerationError("OMS_GENERATION_SEND_INDEX_DUPLICATE")
            take_parent = parent_row is not None and (tail_key is None or parent_key < tail_key)
            if take_parent:
                v1._write_all(fd, parent_row)
                previous_parent = parent_key
                count += 1
                try:
                    parent_row = next(parent_iter)
                except StopIteration:
                    parent_row = None
            else:
                v1._write_all(fd, tail_lines[tail_index])
                tail_index += 1
                count += 1
        v1._fsync(fd)
    finally:
        os.close(fd)
    return count


def _write_merged_indexes(generation_dir: Path, parent_dir: Path | None,
                          updates: dict[tuple[str, str, str], dict[str, Any]],
                          tail_attempts: list[dict[str, Any]], history_base: int) -> tuple[int, int]:
    update_rows = {tuple(v1._hex(x) for x in key): record for key, record in updates.items()}
    ordered_updates = sorted(update_rows.items())
    runtime_path = generation_dir / "runtime-command-index.tsv"
    legacy_path = generation_dir / "command-index.tsv"
    runtime_fd = os.open(runtime_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    legacy_fd = os.open(legacy_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    count = 0
    try:
        parent_iter: Iterable[bytes] = () if parent_dir is None else _iter_private_lines(parent_dir / "runtime-command-index.tsv")
        updates_index = 0
        previous: tuple[str, str, str] | None = None
        for line in parent_iter:
            key, parent_record, _ = _runtime_row(line)
            if previous is not None and key <= previous:
                raise v1.GenerationError("OMS_GENERATION_PARENT_INDEX_ORDER_INVALID")
            previous = key
            while updates_index < len(ordered_updates) and ordered_updates[updates_index][0] < key:
                _, record = ordered_updates[updates_index]
                encoded = v1._runtime_index_line(record)
                v1._write_all(runtime_fd, encoded)
                v1._write_all(legacy_fd, b"\t".join(encoded.rstrip(b"\n").split(b"\t")[:10]) + b"\n")
                count += 1
                updates_index += 1
            if updates_index < len(ordered_updates) and ordered_updates[updates_index][0] == key:
                record = _merge_command_record(parent_record, ordered_updates[updates_index][1])
                encoded = v1._runtime_index_line(record)
                updates_index += 1
            else:
                encoded = line
            v1._write_all(runtime_fd, encoded)
            v1._write_all(legacy_fd, b"\t".join(encoded.rstrip(b"\n").split(b"\t")[:10]) + b"\n")
            count += 1
        while updates_index < len(ordered_updates):
            _, record = ordered_updates[updates_index]
            encoded = v1._runtime_index_line(record)
            v1._write_all(runtime_fd, encoded)
            v1._write_all(legacy_fd, b"\t".join(encoded.rstrip(b"\n").split(b"\t")[:10]) + b"\n")
            count += 1
            updates_index += 1
        v1._fsync(runtime_fd)
        v1._fsync(legacy_fd)
    finally:
        os.close(runtime_fd)
        os.close(legacy_fd)
    send_count = _write_merged_send_index(
        generation_dir, parent_dir, tail_attempts, history_base)
    return count, send_count



SIMULATOR_STATE_META = "simulator_state_checkpoint"
SIMULATOR_STATE_POSITION = "simulator_position_checkpoint"
SIMULATOR_STATE_READY = "simulator_state_checkpoint_ready"


def _empty_simulator_state() -> dict[str, Any]:
    return {"present": False, "max_order_id": 999999,
            "admitted_orders": 0, "positions": {}}


def _simulator_checkpoint_from_hot(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    state: dict[str, Any] | None = None
    positions: dict[str, float] = {}
    ready = False
    for event in events:
        kind = event.get("event", "")
        if kind == SIMULATOR_STATE_META:
            if (state is not None or ready or event.get("venue") != "SIMULATOR" or
                    event.get("account") != "SIM"):
                raise v1.GenerationError("OMS_SIMULATOR_CHECKPOINT_INVALID")
            maximum = event.get("order_id", -1)
            admitted = event.get("broker_request_id", -1)
            if (type(maximum) is not int or maximum < 999999 or
                    type(admitted) is not int or admitted < 0):
                raise v1.GenerationError("OMS_SIMULATOR_CHECKPOINT_INVALID")
            state = {"present": True, "max_order_id": maximum,
                     "admitted_orders": admitted, "positions": positions}
            continue
        if kind == SIMULATOR_STATE_POSITION:
            if (state is None or ready or event.get("venue") != "SIMULATOR" or
                    event.get("account") != "SIM"):
                raise v1.GenerationError("OMS_SIMULATOR_CHECKPOINT_INVALID")
            instrument = event.get("instrument", "")
            side = event.get("side", "")
            quantity = event.get("qty", 0.0)
            if (not instrument or instrument in positions or side not in {"BUY", "SELL"} or
                    isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or
                    not math.isfinite(float(quantity)) or float(quantity) <= 0.0):
                raise v1.GenerationError("OMS_SIMULATOR_CHECKPOINT_INVALID")
            positions[instrument] = float(quantity) if side == "BUY" else -float(quantity)
            continue
        if kind == SIMULATOR_STATE_READY:
            if (state is None or ready or event.get("venue") != "SIMULATOR" or
                    event.get("account") != "SIM" or
                    event.get("order_id") != state["max_order_id"] or
                    event.get("broker_request_id") != state["admitted_orders"]):
                raise v1.GenerationError("OMS_SIMULATOR_CHECKPOINT_INVALID")
            ready = True
    return state if ready else None


def _apply_simulator_events(state: dict[str, Any], events: list[dict[str, Any]], *,
                            require_new_order_ids: bool) -> dict[str, Any]:
    present = bool(state.get("present", False))
    maximum = int(state["max_order_id"])
    base_maximum = maximum
    admitted = int(state["admitted_orders"])
    positions = dict(state["positions"])
    places: dict[int, dict[str, Any]] = {}
    fills: dict[int, dict[str, Any]] = {}
    for event in events:
        if event.get("venue") != "SIMULATOR" or event.get("account") != "SIM":
            continue
        is_fill = event.get("event") == "status" and event.get("status") == "Filled"
        if event.get("event") != "place_sent" and not is_fill:
            continue
        present = True
        order_id = event.get("order_id", -1)
        if type(order_id) is int and order_id > maximum:
            maximum = order_id
        quantity = event.get("qty", 0.0)
        if (type(order_id) is not int or order_id < 0 or not event.get("instrument") or
                event.get("side") not in {"BUY", "SELL"} or isinstance(quantity, bool) or
                not isinstance(quantity, (int, float)) or not math.isfinite(float(quantity)) or
                float(quantity) <= 0.0):
            raise v1.GenerationError("OMS_SIMULATOR_STATE_EVENT_INVALID")
        if event.get("event") == "place_sent":
            prior = places.get(order_id)
            identity = (event.get("instrument"), event.get("side"), float(quantity),
                        event.get("req_id"), event.get("request_hash"))
            if prior is not None:
                prior_identity = (prior.get("instrument"), prior.get("side"),
                                  float(prior.get("qty", 0.0)),
                                  prior.get("req_id"), prior.get("request_hash"))
                if identity != prior_identity:
                    raise v1.GenerationError("OMS_SIMULATOR_STATE_PLACE_CONFLICT")
                continue
            if require_new_order_ids and order_id <= base_maximum:
                raise v1.GenerationError("OMS_SIMULATOR_STATE_ORDER_ID_REGRESSION")
            places[order_id] = event
            admitted += 1
            continue
        owner = places.get(order_id)
        price = event.get("price", 0.0)
        if (owner is None or isinstance(price, bool) or
                not isinstance(price, (int, float)) or not math.isfinite(float(price)) or
                float(price) <= 0.0 or owner.get("instrument") != event.get("instrument") or
                owner.get("side") != event.get("side") or
                float(owner.get("qty", 0.0)) != float(quantity)):
            raise v1.GenerationError("OMS_SIMULATOR_STATE_FILL_CONFLICT")
        prior = fills.get(order_id)
        if prior is not None:
            if ((prior.get("instrument"), prior.get("side"),
                 float(prior.get("qty", 0.0)), float(prior.get("price", 0.0))) !=
                    (event.get("instrument"), event.get("side"),
                     float(quantity), float(price))):
                raise v1.GenerationError("OMS_SIMULATOR_STATE_FILL_CONFLICT")
            continue
        fills[order_id] = event
        instrument = event["instrument"]
        positions[instrument] = positions.get(instrument, 0.0) + (
            float(quantity) if event["side"] == "BUY" else -float(quantity))
        if not math.isfinite(positions[instrument]):
            raise v1.GenerationError("OMS_SIMULATOR_STATE_POSITION_OVERFLOW")
    return {"present": present, "max_order_id": maximum,
            "admitted_orders": admitted, "positions": positions}


def _simulator_state_projection(state: dict[str, Any]) -> list[dict[str, Any]]:
    if not state.get("present", False):
        return []

    def base(kind: str) -> dict[str, Any]:
        return {
            "schema_version": 4, "event": kind, "ts_ms": 0, "order_id": -1,
            "req_id": "", "client_req_id": "", "trace_id": "",
            "event_id": f"{kind}:v1", "risk_code": "", "venue": "SIMULATOR",
            "strategy": "", "account": "SIM", "execution_domain": "SIM:checkpoint",
            "request_hash": "", "venue_correlation_id": "", "broker_callback_type": "",
            "broker_service_epoch": "", "broker_connection_epoch": 0,
            "broker_request_id": 0, "broker_error_code": 0, "broker_message": "",
            "broker_advanced_order_reject_json": "", "broker_why_held": "",
            "broker_execution_id": "", "broker_remaining_quantity": 0.0,
            "broker_market_cap_price": 0.0, "instrument": "", "side": "",
            "qty": 0.0, "price": 0.0, "status": "", "reason": "", "source": "",
        }

    result: list[dict[str, Any]] = []
    meta = base(SIMULATOR_STATE_META)
    meta["order_id"] = int(state["max_order_id"])
    meta["broker_request_id"] = int(state["admitted_orders"])
    meta["status"] = "complete"
    result.append(meta)
    for instrument, signed in sorted(state["positions"].items()):
        if not math.isfinite(float(signed)):
            raise v1.GenerationError("OMS_SIMULATOR_STATE_POSITION_OVERFLOW")
        if float(signed) == 0.0:
            continue
        row = base(SIMULATOR_STATE_POSITION)
        row["event_id"] = f"{SIMULATOR_STATE_POSITION}:{instrument}"
        row["instrument"] = instrument
        row["side"] = "BUY" if float(signed) > 0.0 else "SELL"
        row["qty"] = abs(float(signed))
        row["status"] = "complete"
        result.append(row)
    ready = base(SIMULATOR_STATE_READY)
    ready["order_id"] = int(state["max_order_id"])
    ready["broker_request_id"] = int(state["admitted_orders"])
    ready["status"] = "complete"
    result.append(ready)
    return result


def _read_generation_segment(root: Path, max_bytes: int, max_records: int,
                             max_record_bytes: int) -> list[dict[str, Any]]:
    path = root / "segment-000001.jsonl"
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not v1._private_regular(info):
            raise v1.GenerationError("OMS_GENERATION_UNSAFE_SEGMENT")
        _, events = _strict_records(fd, 0, info.st_size, max_bytes=max_bytes,
                                    max_records=max_records,
                                    max_record_bytes=max_record_bytes)
        return events
    finally:
        os.close(fd)


def _reconstruct_simulator_state(store: Path, generation: str, *, max_bytes: int,
                                 max_records: int, max_record_bytes: int) -> dict[str, Any]:
    chain = _generation_chain(store, generation)
    state = _empty_simulator_state()
    applied = False
    for item_generation, _ in chain:
        events = _read_generation_segment(
            store / item_generation, max_bytes, max_records, max_record_bytes)
        checkpoint = _simulator_checkpoint_from_hot(events)
        if checkpoint is not None:
            state = checkpoint
            applied = True
            continue
        state = _apply_simulator_events(
            state, events, require_new_order_ids=applied)
        applied = True
    return state


def _runtime_manifest_bytes(*, generation: str, parent_generation: str,
                            parent_manifest_sha256: str,
                            history_records: int, segment_records: int,
                            command_records: int, send_attempt_records: int,
                            hot_replay_records: int, marker: bytes,
                            digests: dict[str, str]) -> bytes:
    lines = [
        RUNTIME_MANIFEST_HEADER,
        f"generation={generation}",
        f"parent_generation={parent_generation or '-'}",
        f"parent_manifest_sha256={parent_manifest_sha256 if parent_generation else '-'}",
        f"history_records={history_records}",
        f"segment_records={segment_records}",
        f"command_records={command_records}",
        f"send_attempt_records={send_attempt_records}",
        f"hot_replay_records={hot_replay_records}",
        f"segment_sha256={digests['segment-000001.jsonl']}",
        f"checkpoint_sha256={digests['checkpoint.json']}",
        f"command_index_sha256={digests['command-index.tsv']}",
        f"runtime_command_index_sha256={digests['runtime-command-index.tsv']}",
        f"send_attempt_index_sha256={digests['send-attempt-index.tsv']}",
        f"send_attempt_index_order={SEND_INDEX_ORDER}",
        f"hot_replay_sha256={digests['hot-replay.jsonl']}",
        f"active_tail_header_bytes={len(marker)}",
        f"active_tail_header_sha256={hashlib.sha256(marker).hexdigest()}",
        "authorization_effect=NONE",
        "paper_authorized=0",
        "live_authorized=0",
    ]
    return ("\n".join(lines) + "\n").encode("ascii")


def seal_generation(journal: Path, store: Path, *, stopped: bool,
                    max_bytes: int = 64 * 1024 * 1024,
                    max_records: int = 65536,
                    max_record_bytes: int = 262144,
                    phase_hook: Callable[[str], None] | None = None) -> dict[str, Any]:
    if not stopped:
        raise v1.GenerationError("OMS_GENERATION_STOP_ALL_WRITERS_REQUIRED")
    phase_hook = phase_hook or (lambda _: None)
    if not journal.is_absolute():
        journal = journal.resolve()
    if not store.is_absolute():
        store = store.resolve()
    if store != Path(str(journal) + ".generations"):
        raise v1.GenerationError("OMS_GENERATION_STORE_PATH_MISMATCH")
    store.mkdir(mode=0o700, parents=False, exist_ok=True)
    if not v1._private_directory(os.stat(store, follow_symlinks=False)):
        raise v1.GenerationError("OMS_GENERATION_PRIVATE_STORE_REQUIRED")
    current = v1._read_current(store)
    generation = f"g-{time.time_ns():020d}-{uuid.uuid4().hex[:12]}"
    generation_dir = store / generation
    generation_dir.mkdir(mode=0o700)
    journal_fd = os.open(journal, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    tail_temp: Path | None = None
    try:
        before = os.fstat(journal_fd)
        if not v1._private_regular(before):
            raise v1.GenerationError("OMS_GENERATION_UNSAFE_JOURNAL")
        fcntl.flock(journal_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        parent_generation, start, history_base, parent_hot, parent_dir, _ = _parent_state(
            store, current, journal_fd, before.st_size, max_bytes, max_records,
            max_record_bytes)
        raw_tail, tail_events = _strict_records(
            journal_fd, start, before.st_size, max_bytes=max_bytes,
            max_records=max_records, max_record_bytes=max_record_bytes)
        after = os.fstat(journal_fd)
        named = os.stat(journal, follow_symlinks=False)
        if v1._identity(before) != v1._identity(after) or v1._identity(after) != v1._identity(named):
            raise v1.GenerationError("OMS_GENERATION_JOURNAL_CHANGED")

        segment = generation_dir / "segment-000001.jsonl"
        segment_fd = os.open(segment, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            for raw in raw_tail:
                v1._write_all(segment_fd, raw)
            v1._fsync(segment_fd)
        finally:
            os.close(segment_fd)

        combined = parent_hot + tail_events
        checkpoint, _, hot_replay, _ = v1._project_hot(combined)
        simulator_state = _simulator_checkpoint_from_hot(parent_hot)
        if simulator_state is None:
            simulator_state = (_reconstruct_simulator_state(
                store, parent_generation, max_bytes=max_bytes,
                max_records=max_records, max_record_bytes=max_record_bytes)
                if parent_generation else _empty_simulator_state())
        simulator_state = _apply_simulator_events(
            simulator_state, tail_events,
            require_new_order_ids=bool(parent_generation))
        hot_replay.extend(_simulator_state_projection(simulator_state))
        _, tail_commands, _, tail_attempts = v1._project_hot(tail_events)
        for record in tail_commands.values():
            record["last_sequence"] = history_base + int(record["last_sequence"])
        command_records, send_attempt_records = _write_merged_indexes(
            generation_dir, parent_dir, tail_commands, tail_attempts, history_base)
        history_records = history_base + len(tail_events)
        checkpoint.update({
            "schema": v1.CHECKPOINT_SCHEMA,
            "last_sequence": history_records,
            "history_records": history_records,
            "parent_generation": parent_generation,
            "segment_records": len(tail_events),
            "hot_replay_records": len(hot_replay),
            "send_attempt_records": send_attempt_records,
            "paper_authorized": False,
            "live_authorized": False,
        })
        v1._atomic_json(generation_dir / "checkpoint.json", checkpoint)
        v1._atomic_json(generation_dir / "command-index.json", {
            "schema": v1.INDEX_SCHEMA, "records": command_records,
            "key": ["agent_id", "session_id", "command_id"],
            "full_request_hash_compared": True,
        })
        hot_path = generation_dir / "hot-replay.jsonl"
        hot_fd = os.open(hot_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            for value in hot_replay:
                encoded = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
                if len(encoded) > max_record_bytes:
                    raise v1.GenerationError("OMS_GENERATION_HOT_RECORD_LIMIT")
                v1._write_all(hot_fd, encoded)
            v1._fsync(hot_fd)
        finally:
            os.close(hot_fd)

        marker = _tail_header(generation)
        parent_manifest_sha256 = (
            v1._sha256_file(store / parent_generation / "manifest.json")[1]
            if parent_generation else ""
        )
        preliminary = (
            "segment-000001.jsonl", "checkpoint.json", "command-index.tsv",
            "command-index.json", "runtime-command-index.tsv",
            "send-attempt-index.tsv", "hot-replay.jsonl",
        )
        files: dict[str, dict[str, Any]] = {}
        digests: dict[str, str] = {}
        for name in preliminary:
            size, digest = v1._sha256_file(generation_dir / name)
            files[name] = {"bytes": size, "sha256": digest}
            digests[name] = digest
        runtime_manifest = _runtime_manifest_bytes(
            generation=generation, parent_generation=parent_generation,
            parent_manifest_sha256=parent_manifest_sha256,
            history_records=history_records, segment_records=len(tail_events),
            command_records=command_records, send_attempt_records=send_attempt_records,
            hot_replay_records=len(hot_replay), marker=marker, digests=digests)
        v1._atomic_bytes(generation_dir / "runtime-manifest.txt", runtime_manifest)
        runtime_size, runtime_digest = v1._sha256_file(generation_dir / "runtime-manifest.txt")
        files["runtime-manifest.txt"] = {"bytes": runtime_size, "sha256": runtime_digest}
        manifest = {
            "schema": SCHEMA, "generation": generation,
            "parent_generation": parent_generation,
            "parent_manifest_sha256": parent_manifest_sha256,
            "history_records": history_records, "segment_records": len(tail_events),
            "command_records": command_records,
            "send_attempt_records": send_attempt_records,
            "hot_replay_records": len(hot_replay),
            "active_tail_header_bytes": len(marker),
            "active_tail_header_sha256": hashlib.sha256(marker).hexdigest(),
            "runtime_manifest_sha256": runtime_digest, "files": files,
            "authorization_effect": "NONE", "paper_authorized": False,
            "live_authorized": False,
        }
        v1._atomic_json(generation_dir / "manifest.json", manifest)
        v1._durable_directory(generation_dir)
        phase_hook("generation-durable")

        manifest_digest = v1._sha256_file(generation_dir / "manifest.json")[1]
        tail_temp = journal.with_name(f".{journal.name}.{uuid.uuid4().hex}.tail.tmp")
        tail_fd = os.open(tail_temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            v1._write_all(tail_fd, marker)
            v1._fsync(tail_fd)
        finally:
            os.close(tail_fd)
        v1._durable_directory(journal.parent)
        phase_hook("tail-ready")
        os.replace(tail_temp, journal)
        tail_temp = None
        v1._durable_directory(journal.parent)
        phase_hook("tail-published")

        pointer = {
            "schema": v1.CURRENT_SCHEMA, "generation": generation,
            "manifest_sha256": manifest_digest,
            "runtime_manifest_sha256": runtime_digest,
        }
        v1._atomic_json(store / "CURRENT", pointer)
        v1._durable_directory(store)
        phase_hook("current-json-durable")
        current_raw = v1._read_private_bytes(store / "CURRENT")
        runtime_current = v1._runtime_current_bytes(
            generation=generation, current_sha256=v1._sha256_bytes(current_raw),
            manifest_sha256=manifest_digest, runtime_manifest_sha256=runtime_digest)
        v1._atomic_bytes(store / "CURRENT.runtime", runtime_current)
        v1._durable_directory(store)
        phase_hook("current-durable")
        return manifest
    finally:
        if tail_temp is not None:
            try:
                tail_temp.unlink()
            except FileNotFoundError:
                pass
        os.close(journal_fd)


def _verify_runtime_current(store: Path, generation: str, manifest_digest: str,
                            runtime_digest: str) -> None:
    current_raw = v1._read_private_bytes(store / "CURRENT")
    current = v1._load_json_private(store / "CURRENT")
    if current != {
        "schema": v1.CURRENT_SCHEMA, "generation": generation,
        "manifest_sha256": manifest_digest,
        "runtime_manifest_sha256": runtime_digest,
    }:
        raise v1.GenerationError("OMS_GENERATION_CURRENT_DIGEST_MISMATCH")
    runtime_current = v1._parse_line_manifest(
        v1._read_private_bytes(store / "CURRENT.runtime"), v1.RUNTIME_CURRENT_HEADER)
    if runtime_current != {
        "generation": generation,
        "current_sha256": hashlib.sha256(current_raw).hexdigest(),
        "manifest_sha256": manifest_digest,
        "runtime_manifest_sha256": runtime_digest,
    }:
        raise v1.GenerationError("OMS_GENERATION_RUNTIME_CURRENT_MISMATCH")


def verify_generation(store: Path, generation: str | None = None,
                      journal: Path | None = None) -> dict[str, Any]:
    if not v1._private_directory(os.stat(store, follow_symlinks=False)):
        raise v1.GenerationError("OMS_GENERATION_PRIVATE_STORE_REQUIRED")
    current = v1._read_current(store)
    if generation is None:
        if current is None:
            raise v1.GenerationError("OMS_GENERATION_CURRENT_MISSING")
        generation = current["generation"]
    manifest = _manifest_for(store, generation)
    if manifest.get("schema") == v1.SCHEMA:
        result = v1.verify_generation(store, generation)
        return {**result, "schema": v1.SCHEMA, "history_records": manifest["journal_records"]}
    if manifest.get("schema") != SCHEMA:
        raise v1.GenerationError("OMS_GENERATION_MANIFEST_INVALID")
    root = store / generation
    if not v1._private_directory(os.stat(root, follow_symlinks=False)):
        raise v1.GenerationError("OMS_GENERATION_DIRECTORY_INVALID")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise v1.GenerationError("OMS_GENERATION_FILE_INVENTORY_INVALID")
    for name, expected in files.items():
        if not isinstance(name, str) or "/" in name or not isinstance(expected, dict):
            raise v1.GenerationError("OMS_GENERATION_FILE_INVENTORY_INVALID")
        size, digest = v1._sha256_file(root / name)
        if size != expected.get("bytes") or digest != expected.get("sha256"):
            raise v1.GenerationError("OMS_GENERATION_DIGEST_MISMATCH")
    runtime_raw = v1._read_private_bytes(root / "runtime-manifest.txt")
    runtime = v1._parse_line_manifest(runtime_raw, RUNTIME_MANIFEST_HEADER)
    required_legacy = {
        "generation", "parent_generation", "parent_manifest_sha256", "history_records", "segment_records",
        "command_records", "send_attempt_records", "hot_replay_records",
        "segment_sha256", "checkpoint_sha256", "command_index_sha256",
        "runtime_command_index_sha256", "send_attempt_index_sha256",
        "hot_replay_sha256", "active_tail_header_bytes", "active_tail_header_sha256",
        "authorization_effect", "paper_authorized", "live_authorized",
    }
    required_sorted = set(required_legacy)
    required_sorted.add("send_attempt_index_order")
    sorted_send_index = set(runtime) == required_sorted
    if (set(runtime) not in {frozenset(required_legacy), frozenset(required_sorted)} or
            (sorted_send_index and runtime["send_attempt_index_order"] != SEND_INDEX_ORDER) or
            runtime["generation"] != generation or runtime["authorization_effect"] != "NONE" or
            runtime["paper_authorized"] != "0" or runtime["live_authorized"] != "0"):
        raise v1.GenerationError("OMS_GENERATION_RUNTIME_MANIFEST_INVALID")
    marker = _tail_header(generation)
    expected = {
        "history_records": manifest.get("history_records"),
        "segment_records": manifest.get("segment_records"),
        "command_records": manifest.get("command_records"),
        "send_attempt_records": manifest.get("send_attempt_records"),
        "hot_replay_records": manifest.get("hot_replay_records"),
    }
    for name, value in expected.items():
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
        "segment_sha256": "segment-000001.jsonl",
        "checkpoint_sha256": "checkpoint.json",
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

    runtime_count = 0
    previous = None
    for line in _iter_private_lines(root / "runtime-command-index.tsv"):
        key, _, _ = _runtime_row(line)
        if previous is not None and key <= previous:
            raise v1.GenerationError("OMS_GENERATION_RUNTIME_INDEX_ORDER_INVALID")
        previous = key
        runtime_count += 1
    if runtime_count != manifest.get("command_records"):
        raise v1.GenerationError("OMS_GENERATION_RUNTIME_INDEX_COUNT_MISMATCH")

    send_count = 0
    previous_send = None
    for line in _iter_private_lines(root / "send-attempt-index.tsv"):
        key = _send_key(line)
        if sorted_send_index and previous_send is not None and key <= previous_send:
            raise v1.GenerationError("OMS_GENERATION_SEND_INDEX_ORDER_INVALID")
        previous_send = key
        send_count += 1
    if send_count != manifest.get("send_attempt_records"):
        raise v1.GenerationError("OMS_GENERATION_SEND_INDEX_COUNT_MISMATCH")

    if current and current["generation"] == generation:
        manifest_digest = v1._sha256_file(root / "manifest.json")[1]
        _verify_runtime_current(store, generation, manifest_digest,
                                manifest["runtime_manifest_sha256"])
        if journal is not None:
            fd = os.open(journal, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                info = os.fstat(fd)
                parsed = _parse_tail_header(fd, info.st_size)
                if parsed is None or parsed[0] != generation or parsed[1] != len(marker):
                    raise v1.GenerationError("OMS_ACTIVE_TAIL_LINEAGE_MISMATCH")
            finally:
                os.close(fd)
    parent = manifest.get("parent_generation") or ""
    if parent:
        parent_manifest = _manifest_for(store, parent)
        if (parent_manifest.get("generation") != parent or
                v1._sha256_file(store / parent / "manifest.json")[1] != parent_manifest_sha256):
            raise v1.GenerationError("OMS_GENERATION_PARENT_INVALID")
    return {
        "schema": SCHEMA, "result": "PASS", "generation": generation,
        "history_records": manifest["history_records"],
        "segment_records": manifest["segment_records"],
        "command_records": manifest["command_records"],
        "send_attempt_records": manifest["send_attempt_records"],
        "hot_replay_records": manifest["hot_replay_records"],
        "authorization_effect": "NONE",
    }


def _generation_chain(store: Path, current_generation: str) -> list[tuple[str, dict[str, Any]]]:
    chain: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    generation = current_generation
    while generation:
        if generation in seen:
            raise v1.GenerationError("OMS_GENERATION_PARENT_CHAIN_INVALID")
        seen.add(generation)
        manifest = _manifest_for(store, generation)
        chain.append((generation, manifest))
        parent = manifest.get("parent_generation") or ""
        if not isinstance(parent, str):
            raise v1.GenerationError("OMS_GENERATION_PARENT_INVALID")
        generation = parent
    chain.reverse()
    return chain


def export_legacy(journal: Path, store: Path, output: Path) -> dict[str, Any]:
    current = v1._read_current(store)
    if current is None:
        raise v1.GenerationError("OMS_GENERATION_CURRENT_MISSING")
    generation = current["generation"]
    verify_generation(store, generation, journal if _manifest_for(store, generation).get("schema") == SCHEMA else None)
    chain = _generation_chain(store, generation)
    base_index = 0
    for i, (_, manifest) in enumerate(chain):
        if manifest.get("schema") == v1.SCHEMA:
            base_index = i
    selected = chain[base_index:]
    output_parent = output.parent.resolve()
    output_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not v1._private_directory(os.stat(output_parent, follow_symlinks=False)):
        raise v1.GenerationError("OMS_GENERATION_EXPORT_PRIVATE_DIRECTORY_REQUIRED")
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    records = 0
    total = 0
    try:
        for gen, manifest in selected:
            verify_generation(store, gen)
            segment = store / gen / "segment-000001.jsonl"
            source = os.open(segment, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                info = os.fstat(source)
                offset = 0
                while offset < info.st_size:
                    block = os.pread(source, min(1024 * 1024, info.st_size - offset), offset)
                    if not block:
                        raise v1.GenerationError("OMS_GENERATION_EXPORT_IO_FAILURE")
                    v1._write_all(fd, block)
                    records += block.count(b"\n")
                    total += len(block)
                    offset += len(block)
            finally:
                os.close(source)
        active = os.open(journal, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            info = os.fstat(active)
            current_manifest = chain[-1][1]
            if current_manifest.get("schema") == SCHEMA:
                parsed = _parse_tail_header(active, info.st_size)
                if parsed is None or parsed[0] != generation:
                    raise v1.GenerationError("OMS_ACTIVE_TAIL_LINEAGE_MISMATCH")
                offset = parsed[1]
            else:
                offset = int(current_manifest["journal_prefix_bytes"])
            while offset < info.st_size:
                block = os.pread(active, min(1024 * 1024, info.st_size - offset), offset)
                if not block:
                    raise v1.GenerationError("OMS_GENERATION_EXPORT_IO_FAILURE")
                v1._write_all(fd, block)
                records += block.count(b"\n")
                total += len(block)
                offset += len(block)
        finally:
            os.close(active)
        v1._fsync(fd)
    except BaseException:
        os.close(fd)
        try:
            output.unlink()
        except FileNotFoundError:
            pass
        raise
    os.close(fd)
    v1._durable_directory(output_parent)
    check = os.open(output, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(check)
        observed = 0
        for _raw, _event in validated_raw_records(
                check, info.st_size, max_bytes=max(total, 1),
                max_records=max(records, 1), max_record_bytes=1024 * 1024):
            observed += 1
        if observed != records:
            raise v1.GenerationError("OMS_GENERATION_EXPORT_RECORD_MISMATCH")
    finally:
        os.close(check)
    return {"schema": "heptatrader.oms-downgrade-export.v1", "result": "PASS",
            "generation": generation, "records": records, "bytes": total,
            "authorization_effect": "NONE"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    seal = sub.add_parser("seal")
    seal.add_argument("--journal", type=Path, required=True)
    seal.add_argument("--store", type=Path, required=True)
    seal.add_argument("--stopped-state", action="store_true")
    verify = sub.add_parser("verify")
    verify.add_argument("--journal", type=Path)
    verify.add_argument("--store", type=Path, required=True)
    verify.add_argument("--generation")
    export = sub.add_parser("export")
    export.add_argument("--journal", type=Path, required=True)
    export.add_argument("--store", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "seal":
            result = seal_generation(args.journal, args.store, stopped=args.stopped_state)
        elif args.command == "verify":
            result = verify_generation(args.store, args.generation, args.journal)
        else:
            result = export_legacy(args.journal, args.store, args.output)
    except (OSError, ValueError, OverflowError) as error:
        code = str(error) if isinstance(error, (v1.GenerationError, JournalError)) else type(error).__name__
        print(json.dumps({"result": "FAIL", "reason": code,
                          "authorization_effect": "NONE"}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
