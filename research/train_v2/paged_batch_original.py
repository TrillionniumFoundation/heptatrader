"""Three frozen, disjoint ORIGINAL PAPER price windows in ONE source scan.

New opt-in interface. The legacy one-window reader is completely unchanged,
including its exact source digest for already frozen plans. No broker I/O.
"""
from __future__ import annotations

import hashlib
import json
import os

from laya_cell.shadow_source import read_source
from laya_cell.source_archive import inventory, verify_prefix, PAGE_SIZE, UNSEALED_BOUND
from .adapters import make_spec
from .causal_ttl import sealed_original_broker_expiry
from .contracts import ContractError
from .paged_price_window import (
    SCHEMA, MAX_PAGES_PER_SCAN, MAX_WINDOW_SPAN_MS, MAX_WINDOW_QUOTES,
    MAX_TOTAL_QUOTE_IDENTITIES, _private, _files, _mid,
)

WINDOWS = 3
BATCH_SCHEMA = "heptatrader.train-v2-single-scan-original-price-windows.v1"


def _validate(period, windows, frozen):
    if (not isinstance(period, str) or len(period) != 64 or
            any(c not in "0123456789abcdef" for c in period)):
        raise ContractError("BATCH_ORIGINAL_PERIOD_INVALID")
    if not isinstance(windows, (tuple, list)) or len(windows) != WINDOWS:
        raise ContractError("BATCH_EXACT_THREE_PREDECLARED_WINDOWS")
    for i, bounds in enumerate(windows):
        if (not isinstance(bounds, (tuple, list)) or len(bounds) != 2 or
                any(type(t) is not int for t in bounds) or
                bounds[0] <= 0 or not bounds[0] < bounds[1] or
                bounds[1] - bounds[0] > MAX_WINDOW_SPAN_MS):
            raise ContractError("BATCH_WINDOW_TIME_OR_DURATION")
        if i and windows[i-1][1] >= bounds[0]:
            raise ContractError("BATCH_WINDOW_ORDER_OR_OVERLAP")
    if frozen is not None and (type(frozen) is not int or
            not 1 <= frozen <= MAX_PAGES_PER_SCAN * PAGE_SIZE + UNSEALED_BOUND):
        raise ContractError("BATCH_FROZEN_PREFIX_BOUND")


def verify_three_windows_once(source_root, period_sha256, windows, *,
                              frozen_prefix_segments=None):
    """Return three views having exactly the original single-window shape.

    Inventory, immutable archive pages, EVERY numeric source hash-chain and
    source epoch are checked once. Selected segments are double-read for the
    original broker quote TTL; unsealed newest quotes never enter any window.
    """
    _validate(period_sha256, windows, frozen_prefix_segments)
    root = _private(source_root)
    source_paths, numeric = _files(root)
    if not numeric:
        raise ContractError("BATCH_ORIGINAL_SOURCE_EMPTY")
    cfg = {"feed_directory": str(root), "period_sha256": period_sha256}
    pinned, pages = inventory(cfg, source_paths)
    if (len(pages) > MAX_PAGES_PER_SCAN or
            len(source_paths)-len(pinned) > UNSEALED_BOUND):
        raise ContractError("BATCH_ARCHIVE_PAGE_OR_UNSEALED_BOUND")
    by_path = {p["path"]: p for p in pinned}
    if len(by_path) != len(pinned):
        raise ContractError("BATCH_ARCHIVE_DUPLICATE_PATH")
    spec = make_spec(period_sha256, "RESEARCH_PAGED_ORIGINAL_PRICE_ONLY",
                     broker_reported_expiry=True)
    selected = [[] for _ in windows]
    seen = set()
    proofs = []
    ended_tail = []
    ended_count = 0
    quotes_count = 0
    bytes_count = 0
    empty_count = 0
    unsealed = False
    for segment, file in numeric:
        bytes_count += file.stat().st_size
        header, quotes, prefix = read_source(file)
        if (header.get("period_sha256") != period_sha256 or
                header.get("instrument") != "EUR.USD" or
                prefix.get("uncommitted_tail_bytes") != 0):
            raise ContractError("BATCH_ORIGINAL_SOURCE_HEADER_CHANGED")
        original_pin = by_path.get(str(file))
        if original_pin is not None and (
                original_pin != prefix or prefix["ended"] is not True):
            raise ContractError("BATCH_ARCHIVED_ORIGINAL_SEGMENT_CHANGED")
        if prefix["ended"] is not True:
            if file != numeric[-1][1]:
                raise ContractError("BATCH_EARLIER_SEGMENT_UNSEALED")
            unsealed = True
            continue
        if original_pin is None:
            ended_tail.append(prefix)
        ended_count += 1
        empty_count += not bool(quotes)
        quotes_count += len(quotes)
        if quotes_count > MAX_TOTAL_QUOTE_IDENTITIES:
            raise ContractError("BATCH_TOTAL_SOURCE_QUOTE_BUDGET")
        matched = [False] * WINDOWS
        for q in quotes:
            if q["receipt"] in seen:
                raise ContractError("BATCH_DUPLICATE_ORIGINAL_RECEIPT")
            seen.add(q["receipt"])
            at = q["known_at_ms"]
            for slot, (start, end) in enumerate(windows):
                if start <= at <= end:
                    matched[slot] = True
                    break
        proofs.append({"segment_start_ns": segment,
                       "original_sha256": prefix["sha256"],
                       "quote_count": len(quotes)})
        if not any(matched):
            continue
        decoded, witness = sealed_original_broker_expiry(file, spec)
        if (len(decoded) != len(quotes) or
                witness["verified_prefix_sha256"] != prefix["sha256"] or
                witness["original_chain_head"] != prefix["chain_head"]):
            raise ContractError("BATCH_DOUBLE_READ_SOURCE_CHANGED")
        for quote in decoded:
            at = quote["available_at_ms"]
            for slot, (start, end) in enumerate(windows):
                if start <= at <= end:
                    selected[slot].append((
                        at, quote["event_at_ms"], quote["valid_until_ms"],
                        quote["source_id"], _mid(quote)))
                    if len(selected[slot]) > MAX_WINDOW_QUOTES:
                        raise ContractError("BATCH_PRICE_WINDOW_ROW_BUDGET")
                    break
    if not ended_count or any(not subset for subset in selected):
        raise ContractError("BATCH_ALL_THREE_SOURCE_WINDOWS_REQUIRED")
    for source in ended_tail:
        verify_prefix(source, sealed=True)
    current_files, current_numeric = _files(root)
    if current_files != source_paths or current_numeric != numeric:
        raise ContractError("BATCH_SOURCE_DIRECTORY_FRONTIER_CHANGED")
    _, current_pages = inventory(cfg, source_paths)
    if current_pages != pages:
        raise ContractError("BATCH_ARCHIVE_PAGE_FRONTIER_CHANGED")
    source_hash = hashlib.sha256(json.dumps(
        proofs, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    frozen_hash = None
    if frozen_prefix_segments is not None:
        if frozen_prefix_segments > ended_count:
            raise ContractError("BATCH_FROZEN_PREFIX_MISSING")
        frozen_hash = hashlib.sha256(json.dumps(
            proofs[:frozen_prefix_segments],
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    all_views = []
    page_shas = [p["sha256"] for p in pages]
    for (start, end), rows in zip(windows, selected):
        rows.sort(key=lambda row: (row[0], row[1], row[3]))
        all_views.append({
            "schema": SCHEMA, "rows": rows,
            "known_times": [row[0] for row in rows],
            "first_available_ms": rows[0][0],
            "last_available_ms": rows[-1][0],
            "source_fingerprint_sha256": source_hash,
            "frozen_original_prefix_segments": frozen_prefix_segments,
            "frozen_original_prefix_fingerprint_sha256": frozen_hash,
            "sealed_successors_after_frozen_prefix": (
                ended_count-frozen_prefix_segments
                if frozen_prefix_segments is not None else None),
            "numeric_segments_total": len(numeric),
            "sealed_segments": ended_count, "empty_segments": empty_count,
            "unsealed_newest_excluded": unsealed,
            "verified_original_bytes": bytes_count,
            "verified_original_quote_ids": quotes_count,
            "window_quote_count": len(rows),
            "window_from_ms": start, "window_until_ms": end,
            "source_archive_page_count": len(pages),
            "source_archive_page_sha256": page_shas,
            "all_original_numeric_segment_proofs_verified": True,
            "not_a_future_prediction": True,
            "actual_broker_fill_or_slippage_attested": False,
            "canonical_owner_enrolled": False,
            "trade_authority": False, "broker_requests_sent": 0,
            "broker_orders_sent": 0,
        })
    return {
        "schema": BATCH_SCHEMA, "views": all_views,
        "full_original_scan_count": 1,
        "sealed_source_fingerprint_sha256": source_hash,
        "frozen_prefix_sha256": frozen_hash,
        "verified_sealed_segments": ended_count,
        "selected_quotes": sum(len(a) for a in selected),
        "independent_trade_observations": 0,
        "model_inference_performed": False,
        "broker_orders_sent": 0,
        "trade_authority": False,
    }
