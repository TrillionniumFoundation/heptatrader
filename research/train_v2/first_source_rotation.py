"""Fail-closed original PAPER first-source cohort across numeric segment rotation.

No model, broker, owner key, signature or trading operation.
This V2 boundary never alters previous Phase 4T original source workers
or old signed packet hash/protocol.
"""
from __future__ import annotations

import os
import re
import stat
import time
from pathlib import Path

from .contracts import ContractError, digest

NAME = re.compile(r"segment-([0-9]{19})-([0-9a-f]{8,64})\Z")
POLICY = "PIN_AT_PRIOR_REGISTRATION_REFUSE_ROTATION_V2"
MAX_SEGMENTS = 1024
MAX_CLOCK_AHEAD_NS = 3_000_000_000


def numeric_segments(source_root, *, actual_now_ns=None, historical_asof=False):
    root = Path(source_root)
    if (not root.is_absolute() or root.is_symlink() or not root.is_dir()
            or root.stat().st_uid != os.geteuid()
            or root.stat().st_mode & 0o077):
        raise ContractError("COHORT_ORIGINAL_ROOT_NOT_PRIVATE")
    now = time.time_ns() if actual_now_ns is None else actual_now_ns
    if type(now) is not int or now < 1:
        raise ContractError("COHORT_CLOCK_INVALID")
    entries = []
    # Enumerate segment directories, not just existing quotes.jsonl files:
    # a newly created numeric writer directory with no file yet is ALREADY a
    # possible rollover. Silently skipping it would misidentify firstness.
    for folder in root.glob("segment-*"):
        match = NAME.fullmatch(folder.name)
        if match is None:
            if re.match(r"segment-[0-9]{19}", folder.name):
                raise ContractError("COHORT_AMBIGUOUS_NUMERIC_SEGMENT_NAME")
            continue
        if folder.is_symlink() or not folder.is_dir():
            raise ContractError("COHORT_SEGMENT_SYMLINK")
        path = folder / "quotes.jsonl"
        if path.is_symlink() or not path.is_file():
            raise ContractError("COHORT_NUMERIC_WRITER_WITHOUT_SOURCE")
        st = path.stat()
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid()
                or st.st_mode & 0o077):
            raise ContractError("COHORT_SEGMENT_NOT_PRIVATE")
        start_ns = int(match.group(1))
        if start_ns > now + MAX_CLOCK_AHEAD_NS and not historical_asof:
            raise ContractError("COHORT_SEGMENT_START_IN_FUTURE")
        entries.append((start_ns, path, st.st_mtime_ns))
        if len(entries) > MAX_SEGMENTS:
            raise ContractError("COHORT_SEGMENT_INVENTORY_LIMIT")
    entries.sort(key=lambda row: (row[0], str(row[1])))
    if any(b[0] == a[0] for a, b in zip(entries, entries[1:])):
        raise ContractError("COHORT_DUPLICATE_NUMERIC_START")
    return entries


def pin_first_segment(source_root, start_ms, *, asof_ms=None, historical_asof=False):
    """One unchanged writer segment from preregistration through first input.

    Call with the ORIGINAL durable registration timestamp, strictly before
    the future window starts. Any later writer segment creation, whether
    during the prewarm lead or inside the future window, HOLDS instead
    of resampling. Older segments must have stopped changing by REGISTER time.
    This uses local fs mtime as an additional fail-closed guard, NOT an
    independently attested clock; local-UID file/clock compromise remains.
    """
    if type(start_ms) is not int or start_ms <= 0:
        raise ContractError("COHORT_INVALID_START")
    now_ms = time.time_ns() // 1000000 if asof_ms is None else asof_ms
    if type(now_ms) is not int or now_ms < start_ms:
        raise ContractError("COHORT_WINDOW_NOT_OPEN")
    entries = numeric_segments(
        source_root, actual_now_ns=now_ms * 1000000,
        historical_asof=historical_asof)
    active = [row for row in entries if row[0] <= now_ms * 1000000]
    before_start = [row for row in active if row[0] <= start_ms * 1000000]
    if not before_start:
        raise ContractError("COHORT_NO_PREWINDOW_NUMERIC_SEGMENT")
    selected = before_start[-1]
    if any(row[0] > start_ms * 1000000 for row in active):
        raise ContractError("COHORT_ROTATED_AFTER_DECLARED_WINDOW_START")
    if any(row[2] >= start_ms * 1000000 for row in before_start[:-1]):
        raise ContractError("COHORT_PREDECESSOR_MODIFIED_IN_WINDOW")
    result = {
        "schema": "heptatrader.train-v2-pinned-original-first-segment.v2",
        "selection_policy": POLICY,
        "preregistered_source_anchor_ms": start_ms,
        "selected_segment_start_ns": selected[0],
        "selected_segment_path": str(selected[1]),
        "prior_numeric_segment_count": len(before_start) - 1,
        "numeric_segments_created_after_start_before_asof": 0,
        "legacy_recovery_segment_selected": False,
        "claim_scope": "ONE_LOCAL_WRITER_AT_ONE_START_NO_ROTATION",
        "broker_orders_sent": 0,
        "native_canonical_model_decision": False,
    }
    return {**result, "cohort_id": digest(result)}


def candidate_segment(source_root, start_ms, *, at_ms=None):
    receipt = pin_first_segment(source_root, start_ms, asof_ms=at_ms)
    return Path(receipt["selected_segment_path"]), receipt


def recheck_presealed_cohort(source_root, frozen, *, at_ms=None):
    if (not isinstance(frozen, dict)
            or frozen.get("selection_policy") != POLICY
            or type(frozen.get("source_anchor_at_ms")) is not int):
        raise ContractError("COHORT_PRESEALED_RECORD_INVALID")
    latest = pin_first_segment(
        source_root, frozen["source_anchor_at_ms"], asof_ms=at_ms)
    if (latest["cohort_id"] != frozen.get("cohort_id")
            or latest["selected_segment_path"] != frozen.get("selected_segment_path")
            or latest["selected_segment_start_ns"] != frozen.get("selected_segment_start_ns")):
        raise ContractError("COHORT_PRESEALED_IDENTITY_CHANGED")
    return latest
