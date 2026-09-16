#!/usr/bin/env python3
"""Build and verify stopped-state OMS generations without expiring identity.

A generation contains a byte-identical decoded journal segment, a sorted full-key
command index, a hot-state checkpoint, runtime recovery sidecars and a digest-bound
manifest. CURRENT is published only after every generation file and directory are
durable. CURRENT.runtime then binds the exact CURRENT bytes to the native runtime
manifest; a crash between the two publications therefore fails closed rather than
silently selecting an older generation.

The active journal remains byte-for-byte intact. Native runtime recovery verifies
that the selected generation is an exact prefix of that journal, reconstructs only
hot state, and replays the suffix. Historical command identity remains on disk and
is never expired. This tool never deletes ledger history or grants trading authority.
"""
from __future__ import annotations

import argparse
import bisect
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time
import uuid
from typing import Any, Callable, Iterable

from verify_oms_journal_replay import JournalError, validated_raw_records

SCHEMA = "heptatrader.oms-generation.v1"
CHECKPOINT_SCHEMA = "heptatrader.oms-hot-checkpoint.v1"
INDEX_SCHEMA = "heptatrader.oms-command-index.v1"
CURRENT_SCHEMA = "heptatrader.oms-current.v1"
RUNTIME_MANIFEST_HEADER = "HEPTA_OMS_RUNTIME_GENERATION_V1"
RUNTIME_CURRENT_HEADER = "HEPTA_OMS_RUNTIME_CURRENT_V1"
MAX_GENERATION_FILES = 24
MAX_INDEX_LINE = 64 * 1024


class GenerationError(ValueError):
    pass


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
            info.st_uid, info.st_gid, info.st_mtime_ns, info.st_ctime_ns)


def _private_regular(info: os.stat_result) -> bool:
    return (stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and
            info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o600)


def _private_directory(info: os.stat_result) -> bool:
    return (stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and
            stat.S_IMODE(info.st_mode) == 0o700)


def _fsync(fd: int) -> None:
    while True:
        try:
            os.fsync(fd)
            return
        except InterruptedError:
            pass


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        count = os.write(fd, view)
        if count <= 0:
            raise OSError("short write")
        view = view[count:]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> tuple[int, str]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if not _private_regular(before):
            raise GenerationError("OMS_GENERATION_UNSAFE_FILE")
        digest = hashlib.sha256()
        total = 0
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            total += len(block)
            digest.update(block)
        after = os.fstat(fd)
        named = os.stat(path, follow_symlinks=False)
        if _identity(before) != _identity(after) or _identity(after) != _identity(named):
            raise GenerationError("OMS_GENERATION_FILE_CHANGED")
        return total, digest.hexdigest()
    finally:
        os.close(fd)


def _hex(value: str) -> str:
    return value.encode("utf-8").hex()


def _unhex(value: str) -> str:
    try:
        return bytes.fromhex(value).decode("utf-8")
    except (ValueError, UnicodeError) as error:
        raise GenerationError("OMS_GENERATION_INDEX_ENCODING_INVALID") from error


def _agent_id(source: str) -> str:
    # Current ExecutionCoordinator writes agent.tool:<id>.  The shorter agent:
    # prefix is retained only for historical fixtures that predate that source
    # namespace.  Never strip an arbitrary colon-delimited producer name.
    for prefix in ("agent.tool:", "agent:"):
        if source.startswith(prefix):
            return source[len(prefix):]
    return ""


def _command_id(event: dict[str, Any]) -> str:
    return event.get("req_id") or event.get("client_req_id") or event.get("event_id", "")


def _key(event: dict[str, Any]) -> tuple[str, str, str]:
    return (_agent_id(event.get("source", "")), event.get("trace_id", ""), _command_id(event))


def _operation(event: dict[str, Any], prior: str = "") -> str:
    kind = event.get("event", "")
    if kind.startswith("flatten_") or kind == "flatten_noop":
        return "flatten"
    if kind.startswith("cancel_") or kind == "cancel":
        return "cancel"
    if kind in {"order_intent", "place_send_attempt", "place_sent", "place_activated",
                "place_outcome_uncertain", "execution_command_resolved"}:
        return prior or "place"
    return prior


def _status(event: dict[str, Any], operation: str, prior: str = "unknown") -> str:
    kind, state = event.get("event", ""), event.get("status", "")
    if kind in {"order_intent", "flatten_intent", "place_send_attempt", "flatten_send_attempt",
                "place_outcome_uncertain", "flatten_outcome_uncertain"}:
        return "uncertain"
    if kind in {"reject", "flatten_reject"}:
        return "rejected"
    if kind == "cancel_send_attempt" or (kind == "cancel" and state in {"intent_recorded", "cancel_pending"}):
        return "uncertain"
    if kind == "cancel" and state == "cancel_sent":
        return "accepted"
    if kind in {"place_sent", "place_activated", "flatten_sent", "flatten_noop"}:
        return "accepted"
    if kind in {"execution_command_resolved", "cancel_command_resolved"}:
        return "accepted" if state == "accepted" else "rejected"
    if kind == "execution_projection_failed":
        return "uncertain"
    return prior


def _durable_intent(event: dict[str, Any]) -> bool:
    return (event.get("event") in {"order_intent", "flatten_intent"} or
            (event.get("event") == "cancel" and event.get("status") == "intent_recorded"))


def _update_command(commands: dict[tuple[str, str, str], dict[str, Any]],
                    event: dict[str, Any], sequence: int) -> None:
    key = _key(event)
    if not all(key):
        return
    record = commands.setdefault(key, {
        "agent_id": key[0], "session_id": key[1], "command_id": key[2],
        "request_hash": "", "operation": "", "status": "unknown", "order_id": -1,
        "reason": "", "venue_correlation_id": "", "last_sequence": 0,
        "account": "", "execution_domain": "", "durable_mutation_intent": False,
    })
    request_hash = event.get("request_hash", "")
    if record["request_hash"] and request_hash and record["request_hash"] != request_hash:
        raise GenerationError("OMS_GENERATION_COMMAND_HASH_CONFLICT")
    if request_hash:
        record["request_hash"] = request_hash
    record["operation"] = _operation(event, record["operation"])
    record["status"] = _status(event, record["operation"], record["status"])
    order_id = event.get("order_id", -1)
    if isinstance(order_id, int) and order_id >= 0:
        record["order_id"] = order_id
    if event.get("risk_code"):
        record["reason"] = event["risk_code"]
    if event.get("venue_correlation_id"):
        record["venue_correlation_id"] = event["venue_correlation_id"]
    if event.get("account"):
        record["account"] = event["account"]
    if event.get("execution_domain"):
        record["execution_domain"] = event["execution_domain"]
    record["durable_mutation_intent"] = bool(
        record["durable_mutation_intent"] or _durable_intent(event))
    record["last_sequence"] = sequence


def _project_hot(events: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[tuple[str, str, str], dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    commands: dict[tuple[str, str, str], dict[str, Any]] = {}
    fenced: set[tuple[str, str]] = set()
    recovery_only: dict[tuple[str, str], str] = {}
    active_owners: dict[int, dict[str, str]] = {}
    active_owner_event: dict[int, int] = {}
    fence_event: dict[tuple[str, str], int] = {}
    recovery_event: dict[tuple[str, str], int] = {}
    terminal_fence_event: int | None = None
    projection_failure_event: int | None = None
    send_attempts: dict[tuple[str, str, str], dict[str, Any]] = {}

    for sequence, event in enumerate(events, 1):
        agent = _agent_id(event.get("source", ""))
        session = event.get("trace_id", "")
        kind = event.get("event", "")
        if agent and session:
            owner = (agent, session)
            if kind == "session_owner_fenced":
                fenced.add(owner)
                fence_event[owner] = sequence - 1
            elif kind == "session_owner_fence_release":
                fenced.discard(owner)
                fence_event.pop(owner, None)
            elif kind == "session_owner_recovery_only":
                recovery_only[owner] = event.get("status", "")
                recovery_event[owner] = sequence - 1
        order_id = event.get("order_id", -1)
        if kind in {"place_sent", "place_activated", "flatten_sent"} and order_id >= 0 and agent:
            active_owners[order_id] = {
                "agent_id": agent, "session_id": session,
                "account": event.get("account", ""),
                "execution_domain": event.get("execution_domain", ""),
                "instrument": event.get("instrument", ""), "side": event.get("side", ""),
            }
            active_owner_event[order_id] = sequence - 1
        elif kind == "order_owner_reconciled_terminal" and order_id >= 0:
            active_owners.pop(order_id, None)
            active_owner_event.pop(order_id, None)
        if kind == "paper_terminal_fence":
            terminal_fence_event = sequence - 1
        elif kind == "execution_projection_failed":
            projection_failure_event = sequence - 1
        elif kind == "execution_projection_resolved":
            projection_failure_event = None

        key = _key(event)
        if (all(key) and key not in send_attempts and
                kind in {"place_send_attempt", "place_sent", "flatten_send_attempt", "flatten_sent"}):
            send_attempts[key] = {
                "account": event.get("account", ""),
                "execution_domain": event.get("execution_domain", ""),
                "ts_ms": event.get("ts_ms", 0),
                "agent_id": key[0], "session_id": key[1], "command_id": key[2],
                "sequence": sequence,
            }
        _update_command(commands, event, sequence)

    hot_commands = sorted(
        (value for value in commands.values() if value["status"] == "uncertain"),
        key=lambda value: (value["agent_id"], value["session_id"], value["command_id"]),
    )
    uncertain_keys = {
        (value["agent_id"], value["session_id"], value["command_id"])
        for value in hot_commands
    }
    selected: set[int] = set(active_owner_event.values())
    selected.update(fence_event.values())
    selected.update(recovery_event.values())
    if terminal_fence_event is not None:
        selected.add(terminal_fence_event)
    if projection_failure_event is not None:
        selected.add(projection_failure_event)
    for index, event in enumerate(events):
        if _key(event) in uncertain_keys:
            selected.add(index)
    hot_replay = [events[index] for index in sorted(selected)]
    attempts = sorted(send_attempts.values(), key=lambda value: (
        _hex(value["account"]), _hex(value["execution_domain"]),
        int(value["ts_ms"]), int(value["sequence"]),
        _hex(value["agent_id"]), _hex(value["session_id"]), _hex(value["command_id"])))
    checkpoint = {
        "schema": CHECKPOINT_SCHEMA,
        "last_sequence": len(events),
        "hot_commands": hot_commands,
        "active_order_owners": [{"order_id": order_id, **value}
                                for order_id, value in sorted(active_owners.items())],
        "fenced_owners": [{"agent_id": a, "session_id": s} for a, s in sorted(fenced)],
        "recovery_only_owners": [
            {"agent_id": a, "session_id": s, "fence": fence}
            for (a, s), fence in sorted(recovery_only.items())
        ],
        "hot_replay_records": len(hot_replay),
        "send_attempt_records": len(attempts),
        "paper_authorized": False,
        "live_authorized": False,
    }
    return checkpoint, commands, hot_replay, attempts


def _index_line(record: dict[str, Any]) -> bytes:
    fields = [
        _hex(record["agent_id"]), _hex(record["session_id"]), _hex(record["command_id"]),
        _hex(record["request_hash"]), record["operation"], record["status"],
        str(record["order_id"]), _hex(record["reason"]),
        _hex(record["venue_correlation_id"]), str(record["last_sequence"]),
    ]
    return ("\t".join(fields) + "\n").encode("ascii")


def _runtime_index_line(record: dict[str, Any]) -> bytes:
    fields = [
        _hex(record["agent_id"]), _hex(record["session_id"]), _hex(record["command_id"]),
        _hex(record["request_hash"]), record["operation"], record["status"],
        str(record["order_id"]), _hex(record["reason"]),
        _hex(record["venue_correlation_id"]), str(record["last_sequence"]),
        _hex(record["account"]), _hex(record["execution_domain"]),
        "1" if record["durable_mutation_intent"] else "0",
    ]
    return ("\t".join(fields) + "\n").encode("ascii")


def _send_attempt_line(record: dict[str, Any]) -> bytes:
    fields = [
        _hex(record["account"]), _hex(record["execution_domain"]),
        str(record["ts_ms"]), _hex(record["agent_id"]),
        _hex(record["session_id"]), _hex(record["command_id"]),
        str(record["sequence"]),
    ]
    return ("\t".join(fields) + "\n").encode("ascii")


def _index_key(line: bytes) -> tuple[str, str, str]:
    if len(line) > MAX_INDEX_LINE:
        raise GenerationError("OMS_GENERATION_INDEX_LINE_LIMIT")
    parts = line.rstrip(b"\n").decode("ascii").split("\t")
    if len(parts) != 10:
        raise GenerationError("OMS_GENERATION_INDEX_RECORD_INVALID")
    return (_unhex(parts[0]), _unhex(parts[1]), _unhex(parts[2]))


def _runtime_index_key(line: bytes) -> tuple[str, str, str]:
    if len(line) > MAX_INDEX_LINE:
        raise GenerationError("OMS_GENERATION_RUNTIME_INDEX_LINE_LIMIT")
    parts = line.rstrip(b"\n").decode("ascii").split("\t")
    if len(parts) != 13 or parts[12] not in {"0", "1"}:
        raise GenerationError("OMS_GENERATION_RUNTIME_INDEX_RECORD_INVALID")
    return (parts[0], parts[1], parts[2])


def _atomic_bytes(path: Path, encoded: bytes, *, mode: int = 0o600) -> None:
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode)
    try:
        _write_all(fd, encoded)
        _fsync(fd)
    finally:
        os.close(fd)
    os.replace(temp, path)


def _atomic_json(path: Path, value: Any, *, mode: int = 0o600) -> None:
    encoded = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    _atomic_bytes(path, encoded, mode=mode)


def _durable_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        _fsync(fd)
    finally:
        os.close(fd)


def _load_json_private(path: Path) -> Any:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if not _private_regular(before) or before.st_size > 16 * 1024 * 1024:
            raise GenerationError("OMS_GENERATION_UNSAFE_METADATA")
        raw = os.read(fd, before.st_size + 1)
        if len(raw) != before.st_size:
            raise GenerationError("OMS_GENERATION_METADATA_CHANGED")
        value = json.loads(raw.decode("utf-8"))
        after = os.fstat(fd)
        if _identity(before) != _identity(after):
            raise GenerationError("OMS_GENERATION_METADATA_CHANGED")
        return value
    except (json.JSONDecodeError, UnicodeError, RecursionError) as error:
        raise GenerationError("OMS_GENERATION_METADATA_INVALID") from error
    finally:
        os.close(fd)


def _read_private_bytes(path: Path, maximum: int = 16 * 1024 * 1024) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if not _private_regular(before) or before.st_size < 0 or before.st_size > maximum:
            raise GenerationError("OMS_GENERATION_UNSAFE_METADATA")
        raw = os.read(fd, before.st_size + 1)
        if len(raw) != before.st_size or _identity(before) != _identity(os.fstat(fd)):
            raise GenerationError("OMS_GENERATION_METADATA_CHANGED")
        return raw
    finally:
        os.close(fd)


def _read_current(store: Path) -> dict[str, Any] | None:
    current = store / "CURRENT"
    if not current.exists():
        return None
    value = _load_json_private(current)
    if not isinstance(value, dict) or value.get("schema") != CURRENT_SCHEMA:
        raise GenerationError("OMS_GENERATION_CURRENT_INVALID")
    generation = value.get("generation")
    if not isinstance(generation, str) or not generation:
        raise GenerationError("OMS_GENERATION_CURRENT_INVALID")
    return value


def _runtime_manifest_bytes(*, generation: str, parent_generation: str,
                            journal_prefix_bytes: int, journal_prefix_sha256: str,
                            journal_records: int, command_records: int,
                            send_attempt_records: int, hot_replay_records: int,
                            digests: dict[str, str]) -> bytes:
    lines = [
        RUNTIME_MANIFEST_HEADER,
        f"generation={generation}",
        f"parent_generation={parent_generation or '-'}",
        f"journal_prefix_bytes={journal_prefix_bytes}",
        f"journal_prefix_sha256={journal_prefix_sha256}",
        f"journal_records={journal_records}",
        f"command_records={command_records}",
        f"send_attempt_records={send_attempt_records}",
        f"hot_replay_records={hot_replay_records}",
        f"segment_sha256={digests['segment-000001.jsonl']}",
        f"checkpoint_sha256={digests['checkpoint.json']}",
        f"command_index_sha256={digests['command-index.tsv']}",
        f"runtime_command_index_sha256={digests['runtime-command-index.tsv']}",
        f"send_attempt_index_sha256={digests['send-attempt-index.tsv']}",
        f"hot_replay_sha256={digests['hot-replay.jsonl']}",
        "authorization_effect=NONE",
        "paper_authorized=0",
        "live_authorized=0",
    ]
    return ("\n".join(lines) + "\n").encode("ascii")


def _runtime_current_bytes(*, generation: str, current_sha256: str,
                           manifest_sha256: str, runtime_manifest_sha256: str) -> bytes:
    return ("\n".join([
        RUNTIME_CURRENT_HEADER,
        f"generation={generation}",
        f"current_sha256={current_sha256}",
        f"manifest_sha256={manifest_sha256}",
        f"runtime_manifest_sha256={runtime_manifest_sha256}",
    ]) + "\n").encode("ascii")


def build_generation(journal: Path, store: Path, *, stopped: bool,
                     max_bytes: int = 64 * 1024 * 1024,
                     max_records: int = 65536,
                     max_record_bytes: int = 262144,
                     phase_hook: Callable[[str], None] | None = None) -> dict[str, Any]:
    if not stopped:
        raise GenerationError("OMS_GENERATION_STOP_ALL_WRITERS_REQUIRED")
    phase_hook = phase_hook or (lambda _: None)
    store.mkdir(mode=0o700, parents=False, exist_ok=True)
    if not _private_directory(os.stat(store, follow_symlinks=False)):
        raise GenerationError("OMS_GENERATION_PRIVATE_STORE_REQUIRED")
    current = _read_current(store)
    parent_generation = current["generation"] if current else ""
    generation = f"g-{time.time_ns():020d}-{uuid.uuid4().hex[:12]}"
    generation_dir = store / generation
    generation_dir.mkdir(mode=0o700)
    journal_fd = os.open(journal, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        journal_before = os.fstat(journal_fd)
        if not _private_regular(journal_before):
            raise GenerationError("OMS_GENERATION_UNSAFE_JOURNAL")
        if os.pread(journal_fd, 2, 0) == b"\x1f\x8b":
            raise GenerationError("OMS_GENERATION_RUNTIME_REQUIRES_EXPANDED_JOURNAL")
        fcntl.flock(journal_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        observations: dict[str, Any] = {}
        events: list[dict[str, Any]] = []
        segment = generation_dir / "segment-000001.jsonl"
        segment_fd = os.open(segment, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        logical = hashlib.sha256()
        try:
            for raw, event in validated_raw_records(
                    journal_fd, journal_before.st_size, max_bytes=max_bytes,
                    max_records=max_records, max_record_bytes=max_record_bytes,
                    observations=observations):
                _write_all(segment_fd, raw)
                logical.update(raw)
                events.append(event)
            _fsync(segment_fd)
        finally:
            os.close(segment_fd)
        journal_after = os.fstat(journal_fd)
        named = os.stat(journal, follow_symlinks=False)
        if _identity(journal_before) != _identity(journal_after) or _identity(journal_after) != _identity(named):
            raise GenerationError("OMS_GENERATION_JOURNAL_CHANGED")
        segment_size, segment_digest = _sha256_file(segment)
        if segment_size != journal_before.st_size or segment_digest != logical.hexdigest():
            raise GenerationError("OMS_GENERATION_RUNTIME_PREFIX_MISMATCH")

        checkpoint, commands, hot_replay, send_attempts = _project_hot(events)
        checkpoint["journal_logical_sha256"] = logical.hexdigest()
        checkpoint["journal_records"] = observations["records"]
        checkpoint["journal_prefix_bytes"] = journal_before.st_size
        _atomic_json(generation_dir / "checkpoint.json", checkpoint)

        index = generation_dir / "command-index.tsv"
        index_fd = os.open(index, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            for _, record in sorted(commands.items()):
                _write_all(index_fd, _index_line(record))
            _fsync(index_fd)
        finally:
            os.close(index_fd)
        index_meta = {
            "schema": INDEX_SCHEMA,
            "records": len(commands),
            "key": ["agent_id", "session_id", "command_id"],
            "full_request_hash_compared": True,
        }
        _atomic_json(generation_dir / "command-index.json", index_meta)

        runtime_index = generation_dir / "runtime-command-index.tsv"
        runtime_fd = os.open(runtime_index, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            runtime_records = sorted(commands.values(), key=lambda record: (
                _hex(record["agent_id"]), _hex(record["session_id"]), _hex(record["command_id"])))
            for record in runtime_records:
                _write_all(runtime_fd, _runtime_index_line(record))
            _fsync(runtime_fd)
        finally:
            os.close(runtime_fd)

        attempts_path = generation_dir / "send-attempt-index.tsv"
        attempts_fd = os.open(attempts_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            for record in send_attempts:
                _write_all(attempts_fd, _send_attempt_line(record))
            _fsync(attempts_fd)
        finally:
            os.close(attempts_fd)

        hot_path = generation_dir / "hot-replay.jsonl"
        hot_fd = os.open(hot_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            for value in hot_replay:
                encoded = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
                if len(encoded) > max_record_bytes:
                    raise GenerationError("OMS_GENERATION_HOT_RECORD_LIMIT")
                _write_all(hot_fd, encoded)
            _fsync(hot_fd)
        finally:
            os.close(hot_fd)

        phase_hook("files-written")
        files: dict[str, dict[str, Any]] = {}
        preliminary_names = (
            "segment-000001.jsonl", "checkpoint.json", "command-index.tsv",
            "command-index.json", "runtime-command-index.tsv",
            "send-attempt-index.tsv", "hot-replay.jsonl",
        )
        digests: dict[str, str] = {}
        for name in preliminary_names:
            size, digest = _sha256_file(generation_dir / name)
            files[name] = {"bytes": size, "sha256": digest}
            digests[name] = digest
        runtime_manifest = _runtime_manifest_bytes(
            generation=generation, parent_generation=parent_generation,
            journal_prefix_bytes=journal_before.st_size,
            journal_prefix_sha256=logical.hexdigest(),
            journal_records=observations["records"], command_records=len(commands),
            send_attempt_records=len(send_attempts), hot_replay_records=len(hot_replay),
            digests=digests)
        _atomic_bytes(generation_dir / "runtime-manifest.txt", runtime_manifest)
        runtime_manifest_size, runtime_manifest_digest = _sha256_file(
            generation_dir / "runtime-manifest.txt")
        files["runtime-manifest.txt"] = {
            "bytes": runtime_manifest_size, "sha256": runtime_manifest_digest}

        manifest = {
            "schema": SCHEMA,
            "generation": generation,
            "parent_generation": parent_generation,
            "journal_logical_sha256": logical.hexdigest(),
            "journal_prefix_bytes": journal_before.st_size,
            "journal_records": observations["records"],
            "command_records": len(commands),
            "send_attempt_records": len(send_attempts),
            "hot_replay_records": len(hot_replay),
            "runtime_manifest_sha256": runtime_manifest_digest,
            "files": files,
            "authorization_effect": "NONE",
            "paper_authorized": False,
            "live_authorized": False,
        }
        _atomic_json(generation_dir / "manifest.json", manifest)
        _durable_directory(generation_dir)
        phase_hook("generation-durable")

        manifest_digest = _sha256_file(generation_dir / "manifest.json")[1]
        pointer = {
            "schema": CURRENT_SCHEMA, "generation": generation,
            "manifest_sha256": manifest_digest,
            "runtime_manifest_sha256": runtime_manifest_digest,
        }
        _atomic_json(store / "CURRENT", pointer)
        _durable_directory(store)
        phase_hook("current-json-durable")
        current_raw = _read_private_bytes(store / "CURRENT")
        runtime_current = _runtime_current_bytes(
            generation=generation, current_sha256=_sha256_bytes(current_raw),
            manifest_sha256=manifest_digest,
            runtime_manifest_sha256=runtime_manifest_digest)
        _atomic_bytes(store / "CURRENT.runtime", runtime_current)
        _durable_directory(store)
        phase_hook("current-durable")
        return manifest
    except BaseException:
        # An unpublished directory is inert. Once CURRENT changes, a missing or
        # mismatched CURRENT.runtime makes native startup fail closed; it never
        # authorizes fallback to the parent generation.
        raise
    finally:
        os.close(journal_fd)


def _verify_index(path: Path, expected: int) -> list[bytes]:
    data = path.read_bytes()
    lines = data.splitlines(keepends=True)
    if len(lines) != expected:
        raise GenerationError("OMS_GENERATION_INDEX_COUNT_MISMATCH")
    previous = None
    for line in lines:
        key = _index_key(line)
        if previous is not None and key <= previous:
            raise GenerationError("OMS_GENERATION_INDEX_ORDER_INVALID")
        previous = key
    return lines


def _verify_runtime_index(path: Path, expected: int) -> None:
    lines = path.read_bytes().splitlines(keepends=True)
    if len(lines) != expected:
        raise GenerationError("OMS_GENERATION_RUNTIME_INDEX_COUNT_MISMATCH")
    previous = None
    for line in lines:
        key = _runtime_index_key(line)
        if previous is not None and key <= previous:
            raise GenerationError("OMS_GENERATION_RUNTIME_INDEX_ORDER_INVALID")
        previous = key


def _parse_line_manifest(raw: bytes, header: str) -> dict[str, str]:
    try:
        lines = raw.decode("ascii").splitlines()
    except UnicodeError as error:
        raise GenerationError("OMS_GENERATION_RUNTIME_METADATA_INVALID") from error
    if not lines or lines[0] != header:
        raise GenerationError("OMS_GENERATION_RUNTIME_METADATA_INVALID")
    fields: dict[str, str] = {}
    for line in lines[1:]:
        name, separator, value = line.partition("=")
        if not separator or not name or not value or name in fields:
            raise GenerationError("OMS_GENERATION_RUNTIME_METADATA_INVALID")
        fields[name] = value
    return fields


def verify_generation(store: Path, generation: str | None = None) -> dict[str, Any]:
    if not _private_directory(os.stat(store, follow_symlinks=False)):
        raise GenerationError("OMS_GENERATION_PRIVATE_STORE_REQUIRED")
    current = _read_current(store)
    if generation is None:
        if current is None:
            raise GenerationError("OMS_GENERATION_CURRENT_MISSING")
        generation = current["generation"]
    root = store / generation
    if not _private_directory(os.stat(root, follow_symlinks=False)):
        raise GenerationError("OMS_GENERATION_DIRECTORY_INVALID")
    manifest = _load_json_private(root / "manifest.json")
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA or manifest.get("generation") != generation:
        raise GenerationError("OMS_GENERATION_MANIFEST_INVALID")
    files = manifest.get("files")
    if not isinstance(files, dict) or not 1 <= len(files) <= MAX_GENERATION_FILES:
        raise GenerationError("OMS_GENERATION_FILE_INVENTORY_INVALID")
    for name, expected in files.items():
        if (not isinstance(name, str) or "/" in name or name in {"", ".", ".."} or
                not isinstance(expected, dict)):
            raise GenerationError("OMS_GENERATION_FILE_INVENTORY_INVALID")
        size, digest = _sha256_file(root / name)
        if size != expected.get("bytes") or digest != expected.get("sha256"):
            raise GenerationError("OMS_GENERATION_DIGEST_MISMATCH")
    index_meta = _load_json_private(root / "command-index.json")
    if index_meta.get("schema") != INDEX_SCHEMA or index_meta.get("records") != manifest.get("command_records"):
        raise GenerationError("OMS_GENERATION_INDEX_METADATA_INVALID")
    lines = _verify_index(root / "command-index.tsv", index_meta["records"])
    _verify_runtime_index(root / "runtime-command-index.tsv", index_meta["records"])
    checkpoint = _load_json_private(root / "checkpoint.json")
    if (checkpoint.get("schema") != CHECKPOINT_SCHEMA or
            checkpoint.get("journal_logical_sha256") != manifest.get("journal_logical_sha256") or
            checkpoint.get("journal_prefix_bytes") != manifest.get("journal_prefix_bytes") or
            checkpoint.get("journal_records") != manifest.get("journal_records") or
            checkpoint.get("hot_replay_records") != manifest.get("hot_replay_records") or
            checkpoint.get("send_attempt_records") != manifest.get("send_attempt_records")):
        raise GenerationError("OMS_GENERATION_CHECKPOINT_MISMATCH")
    indexed = {_index_key(line) for line in lines}
    for hot in checkpoint.get("hot_commands", []):
        key = (hot.get("agent_id", ""), hot.get("session_id", ""), hot.get("command_id", ""))
        if key not in indexed:
            raise GenerationError("OMS_GENERATION_HOT_COMMAND_MISSING_FROM_INDEX")
    hot_lines = _read_private_bytes(root / "hot-replay.jsonl").splitlines()
    if len(hot_lines) != manifest.get("hot_replay_records"):
        raise GenerationError("OMS_GENERATION_HOT_REPLAY_COUNT_MISMATCH")
    for line in hot_lines:
        try:
            if not isinstance(json.loads(line.decode("utf-8")), dict):
                raise ValueError("event is not object")
        except (json.JSONDecodeError, UnicodeError, ValueError) as error:
            raise GenerationError("OMS_GENERATION_HOT_REPLAY_INVALID") from error
    send_lines = _read_private_bytes(root / "send-attempt-index.tsv").splitlines()
    if len(send_lines) != manifest.get("send_attempt_records"):
        raise GenerationError("OMS_GENERATION_SEND_INDEX_COUNT_MISMATCH")

    runtime_raw = _read_private_bytes(root / "runtime-manifest.txt")
    runtime = _parse_line_manifest(runtime_raw, RUNTIME_MANIFEST_HEADER)
    expected_runtime = {
        "generation": generation,
        "parent_generation": manifest.get("parent_generation") or "-",
        "journal_prefix_bytes": str(manifest.get("journal_prefix_bytes")),
        "journal_prefix_sha256": manifest.get("journal_logical_sha256"),
        "journal_records": str(manifest.get("journal_records")),
        "command_records": str(manifest.get("command_records")),
        "send_attempt_records": str(manifest.get("send_attempt_records")),
        "hot_replay_records": str(manifest.get("hot_replay_records")),
        "segment_sha256": files["segment-000001.jsonl"]["sha256"],
        "checkpoint_sha256": files["checkpoint.json"]["sha256"],
        "command_index_sha256": files["command-index.tsv"]["sha256"],
        "runtime_command_index_sha256": files["runtime-command-index.tsv"]["sha256"],
        "send_attempt_index_sha256": files["send-attempt-index.tsv"]["sha256"],
        "hot_replay_sha256": files["hot-replay.jsonl"]["sha256"],
        "authorization_effect": "NONE", "paper_authorized": "0", "live_authorized": "0",
    }
    if runtime != expected_runtime or _sha256_bytes(runtime_raw) != manifest.get("runtime_manifest_sha256"):
        raise GenerationError("OMS_GENERATION_RUNTIME_MANIFEST_MISMATCH")

    manifest_digest = _sha256_file(root / "manifest.json")[1]
    if current and current["generation"] == generation:
        if (current.get("manifest_sha256") != manifest_digest or
                current.get("runtime_manifest_sha256") != manifest.get("runtime_manifest_sha256")):
            raise GenerationError("OMS_GENERATION_CURRENT_DIGEST_MISMATCH")
        current_raw = _read_private_bytes(store / "CURRENT")
        runtime_current_raw = _read_private_bytes(store / "CURRENT.runtime")
        runtime_current = _parse_line_manifest(runtime_current_raw, RUNTIME_CURRENT_HEADER)
        if runtime_current != {
            "generation": generation,
            "current_sha256": _sha256_bytes(current_raw),
            "manifest_sha256": manifest_digest,
            "runtime_manifest_sha256": manifest.get("runtime_manifest_sha256"),
        }:
            raise GenerationError("OMS_GENERATION_RUNTIME_CURRENT_MISMATCH")
    parent = manifest.get("parent_generation", "")
    if parent:
        parent_root = store / parent
        if not parent_root.is_dir():
            raise GenerationError("OMS_GENERATION_PARENT_MISSING")
        parent_manifest = _load_json_private(parent_root / "manifest.json")
        if parent_manifest.get("generation") != parent:
            raise GenerationError("OMS_GENERATION_PARENT_INVALID")
    return {"schema": "heptatrader.oms-generation-verification.v1", "result": "PASS",
            "generation": generation, "journal_records": manifest["journal_records"],
            "command_records": manifest["command_records"],
            "send_attempt_records": manifest["send_attempt_records"],
            "hot_replay_records": manifest["hot_replay_records"],
            "authorization_effect": "NONE"}


def lookup_command(store: Path, agent_id: str, session_id: str, command_id: str,
                   request_hash: str) -> dict[str, Any]:
    current = _read_current(store)
    if current is None:
        raise GenerationError("OMS_GENERATION_CURRENT_MISSING")
    verify_generation(store, current["generation"])
    index = store / current["generation"] / "command-index.tsv"
    lines = index.read_bytes().splitlines(keepends=True)
    keys = [_index_key(line) for line in lines]
    wanted = (agent_id, session_id, command_id)
    position = bisect.bisect_left(keys, wanted)
    if position == len(keys) or keys[position] != wanted:
        return {"status": "missing", "authorization_effect": "NONE"}
    parts = lines[position].rstrip(b"\n").decode("ascii").split("\t")
    stored_hash = _unhex(parts[3])
    outcome = "duplicate" if stored_hash == request_hash else "conflict"
    return {
        "status": outcome,
        "operation": parts[4],
        "command_status": parts[5],
        "order_id": int(parts[6]),
        "reason": _unhex(parts[7]),
        "venue_correlation_id": _unhex(parts[8]),
        "last_sequence": int(parts[9]),
        "authorization_effect": "NONE",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--journal", type=Path, required=True)
    build.add_argument("--store", type=Path, required=True)
    build.add_argument("--stopped-state", action="store_true")
    verify = sub.add_parser("verify")
    verify.add_argument("--store", type=Path, required=True)
    verify.add_argument("--generation")
    lookup = sub.add_parser("lookup")
    lookup.add_argument("--store", type=Path, required=True)
    lookup.add_argument("--agent-id", required=True)
    lookup.add_argument("--session-id", required=True)
    lookup.add_argument("--command-id", required=True)
    lookup.add_argument("--request-hash", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            result = build_generation(args.journal, args.store, stopped=args.stopped_state)
        elif args.command == "verify":
            result = verify_generation(args.store, args.generation)
        else:
            result = lookup_command(args.store, args.agent_id, args.session_id,
                                    args.command_id, args.request_hash)
    except (OSError, ValueError, OverflowError) as error:
        code = str(error) if isinstance(error, (GenerationError, JournalError)) else type(error).__name__
        print(json.dumps({"result": "FAIL", "reason": code,
                          "authorization_effect": "NONE"}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
