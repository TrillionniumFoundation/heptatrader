#!/usr/bin/env python3
"""Streaming verifier for V2 OMS generations."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import hepta_oms_checkpoint as v1
import hepta_oms_lifecycle_core as core
import hepta_oms_simulator_recovery as simulator


def _stream_runtime_index(path: Path, expected: int) -> None:
    count = 0
    previous = None
    for line in core._iter_private_lines(path):
        key, _record, _fields = core._runtime_row(line)
        if previous is not None and key <= previous:
            raise v1.GenerationError("OMS_GENERATION_RUNTIME_INDEX_ORDER_INVALID")
        previous = key
        count += 1
    if count != expected:
        raise v1.GenerationError("OMS_GENERATION_RUNTIME_INDEX_COUNT_MISMATCH")


def _stream_send_index(path: Path, expected: int, sorted_send_index: bool) -> None:
    count = 0
    previous = None
    for line in core._iter_private_lines(path):
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
    manifest = core._manifest_for(store, generation)
    if manifest.get("schema") == v1.SCHEMA:
        result = v1.verify_generation(store, generation)
        return {**result, "schema": v1.SCHEMA,
                "history_records": manifest["journal_records"]}
    if manifest.get("schema") != core.SCHEMA:
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
    runtime = v1._parse_line_manifest(runtime_raw, core.RUNTIME_MANIFEST_HEADER)
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
    runtime_names = frozenset(runtime)
    sorted_send_index = runtime_names == frozenset(required_sorted)
    if (runtime_names not in {frozenset(required_legacy), frozenset(required_sorted)} or
            (sorted_send_index and runtime["send_attempt_index_order"] != "account-domain-time-v1") or
            runtime["generation"] != generation or runtime["authorization_effect"] != "NONE" or
            runtime["paper_authorized"] != "0" or runtime["live_authorized"] != "0"):
        raise v1.GenerationError("OMS_GENERATION_RUNTIME_MANIFEST_INVALID")
    marker = core._tail_header(generation)
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

    # Historical indexes can be arbitrarily large.  Verification retains one
    # prior key and a counter, rather than list() materializing all rows.
    _stream_runtime_index(root / "runtime-command-index.tsv", manifest["command_records"])
    _stream_send_index(root / "send-attempt-index.tsv",
                       manifest["send_attempt_records"], sorted_send_index)

    simulator_state = simulator.checkpoint_state(root)
    if simulator_state is not None:
        simulator.encode_state(simulator_state)

    if current and current["generation"] == generation:
        manifest_digest = v1._sha256_file(root / "manifest.json")[1]
        core._verify_runtime_current(store, generation, manifest_digest,
                                     manifest["runtime_manifest_sha256"])
        if journal is not None:
            fd = os.open(journal, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                info = os.fstat(fd)
                parsed = core._parse_tail_header(fd, info.st_size)
                if parsed is None or parsed[0] != generation or parsed[1] != len(marker):
                    raise v1.GenerationError("OMS_ACTIVE_TAIL_LINEAGE_MISMATCH")
            finally:
                os.close(fd)
    parent = manifest.get("parent_generation") or ""
    if parent:
        parent_manifest = core._manifest_for(store, parent)
        if (parent_manifest.get("generation") != parent or
                v1._sha256_file(store / parent / "manifest.json")[1] != parent_manifest_sha256):
            raise v1.GenerationError("OMS_GENERATION_PARENT_INVALID")
    return {
        "schema": core.SCHEMA, "result": "PASS", "generation": generation,
        "history_records": manifest["history_records"],
        "segment_records": manifest["segment_records"],
        "command_records": manifest["command_records"],
        "send_attempt_records": manifest["send_attempt_records"],
        "hot_replay_records": manifest["hot_replay_records"],
        "simulator_recovery": simulator_state is not None,
        "authorization_effect": "NONE",
    }
