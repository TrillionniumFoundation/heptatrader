"""Paged, source-complete ORIGINAL PAPER quote proof for a bounded price window.

No source segment, archive page, writer service, original prediction or
precommitted decision is modified. This is opt-in; the old full-archive loader
retains its exact legacy limit and all frozen verifiers remain unchanged.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from laya_cell.integrity import ContractError
from laya_cell.shadow_source import read_source
from laya_cell.source_archive import (
    PAGE_SIZE, UNSEALED_BOUND, inventory, verify_prefix,
)
from .adapters import make_spec
from .causal_ttl import sealed_original_broker_expiry

SEGMENT_NAME = re.compile(r"^segment-([0-9]{19})-([0-9a-f]{8})$")
SCHEMA = "heptatrader.train-v2-paged-original-price-window.v1"
MAX_PAGES_PER_SCAN = 128
MAX_WINDOW_SPAN_MS = 2 * 60 * 60 * 1000
MAX_WINDOW_QUOTES = 18000
MAX_TOTAL_QUOTE_IDENTITIES = 2_000_000
MAX_SINGLE_SOURCE_BYTES = 64 * 1024 * 1024


def _price(value):
    if isinstance(value, bool):
        raise ContractError("PAGED_PRICE_NONFINITE")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ContractError("PAGED_PRICE_NONFINITE") from exc
    if not result.is_finite() or result <= 0 or result >= Decimal("100000000"):
        raise ContractError("PAGED_PRICE_NONFINITE")
    return result


def _private(root):
    root = Path(root)
    if (not root.is_absolute() or ".." in root.parts
            or root.is_symlink() or not root.is_dir()):
        raise ContractError("PAGED_SOURCE_DIRECTORY_UNSAFE")
    st = root.stat()
    if st.st_uid != os.geteuid() or st.st_mode & 0o077:
        raise ContractError("PAGED_SOURCE_DIRECTORY_NOT_PRIVATE")
    return root


def _files(root):
    # Preserve the complete original source directory inventory: an invalid
    # intermediate segment is not omitted merely because the target is recent.
    paths = []
    numeric = []
    seen_starts = set()
    for directory in sorted(root.iterdir(), key=lambda x: x.name):
        name = directory.name
        if not name.startswith("segment-"):
            continue
        if directory.is_symlink() or not directory.is_dir():
            raise ContractError("PAGED_SOURCE_SEGMENT_NOT_DIRECTORY")
        if name == "segment-recovery-live":
            # The source archive knows this alias, but it is not a numeric
            # originally closed observation used for research outcomes.
            item = directory / "quotes.jsonl"
            if item.is_symlink() or not item.is_file():
                raise ContractError("PAGED_RECOVERY_SOURCE_MISSING")
            paths.append(item)
            continue
        match = SEGMENT_NAME.fullmatch(name)
        if not match:
            raise ContractError("PAGED_SOURCE_MALFORMED_SEGMENT")
        number = int(match.group(1))
        if number in seen_starts:
            raise ContractError("PAGED_SOURCE_DUPLICATE_SEGMENT_START")
        seen_starts.add(number)
        file = directory / "quotes.jsonl"
        if file.is_symlink() or not file.is_file():
            raise ContractError("PAGED_SOURCE_FILE_UNSAFE")
        if (directory.stat().st_uid != os.geteuid()
                or directory.stat().st_mode & 0o077):
            raise ContractError("PAGED_SOURCE_SEGMENT_MODE")
        size = file.stat().st_size
        if not 0 < size <= MAX_SINGLE_SOURCE_BYTES:
            raise ContractError("PAGED_SOURCE_SINGLE_FILE_BUDGET")
        numeric.append((number, file))
        paths.append(file)
    return sorted(paths), sorted(numeric)


def _mid(record):
    bid = _price(record["bid"])
    ask = _price(record["ask"])
    if ask < bid:
        raise ContractError("PAGED_SOURCE_CROSSED_QUOTE")
    return (bid + ask) / 2


def verify_paged_price_window(root, period_sha256, from_ms, until_ms,
                              *, frozen_prefix_segments=None):
    """Verify EVERY original segment but retain quotes only in [from, until].

    Pinned completed Laya pages are checked against their independent
    hash-linked archive ledger and original bytes, including before the
    chosen window. Remaining unpaged tail is bounded by its existing
    128-segment source-archive rule and every ended segment is rechecked.
    """
    root = _private(root)
    if frozen_prefix_segments is not None and (
            type(frozen_prefix_segments) is not int
            or frozen_prefix_segments < 1
            or frozen_prefix_segments > MAX_PAGES_PER_SCAN*PAGE_SIZE+UNSEALED_BOUND):
        raise ContractError("PAGED_SOURCE_FROZEN_PREFIX_COUNT_INVALID")
    if (type(from_ms) is not int or type(until_ms) is not int
            or from_ms <= 0 or not from_ms < until_ms
            or until_ms - from_ms > MAX_WINDOW_SPAN_MS
            or not isinstance(period_sha256, str) or len(period_sha256) != 64
            or any(c not in "0123456789abcdef" for c in period_sha256)):
        raise ContractError("PAGED_WINDOW_TIME_OR_SCOPE")
    original_paths, numeric = _files(root)
    if not numeric:
        raise ContractError("PAGED_SOURCE_NO_NUMERIC_SEGMENTS")
    expected_pins, pages = inventory(
        {"feed_directory": str(root), "period_sha256": period_sha256},
        original_paths,
    )
    if (len(pages) > MAX_PAGES_PER_SCAN or
            len(original_paths) - len(expected_pins) > UNSEALED_BOUND):
        raise ContractError("PAGED_SOURCE_CHECKPOINT_PAGE_BOUND")
    prefix_by_path = {pin["path"]: pin for pin in expected_pins}
    if len(prefix_by_path) != len(expected_pins):
        raise ContractError("PAGED_SOURCE_DUPLICATE_CHECKPOINT_FILE")
    spec = make_spec(period_sha256, "RESEARCH_PAGED_ORIGINAL_PRICE_ONLY",
                     broker_reported_expiry=True)
    proofs = []
    selected = []
    seen_receipts = set()
    ended_tail = []
    sealed = 0
    total_bytes = 0
    total_quotes = 0
    empty = 0
    active_excluded = False
    for number, file in numeric:
        total_bytes += file.stat().st_size
        header, quotes, observed = read_source(file)
        if (header.get("period_sha256") != period_sha256
                or header.get("instrument") != "EUR.USD"
                or observed.get("uncommitted_tail_bytes") != 0):
            raise ContractError("PAGED_SOURCE_ORIGINAL_CHAIN_INVALID")
        pinned = prefix_by_path.get(str(file))
        if pinned is not None and (pinned != observed or observed["ended"] is not True):
            raise ContractError("PAGED_SOURCE_ARCHIVED_SEGMENT_CHANGED")
        if observed["ended"] is not True:
            if file != numeric[-1][1]:
                raise ContractError("PAGED_SOURCE_EARLIER_UNSEALED_SEGMENT")
            active_excluded = True
            continue
        if pinned is None:
            ended_tail.append(observed)
        sealed += 1
        empty += not bool(quotes)
        total_quotes += len(quotes)
        if total_quotes > MAX_TOTAL_QUOTE_IDENTITIES:
            raise ContractError("PAGED_SOURCE_QUOTE_ID_BUDGET")
        matched = False
        for q in quotes:
            rid = q["receipt"]
            if rid in seen_receipts:
                raise ContractError("PAGED_SOURCE_DUPLICATE_ORIGINAL_RECEIPT")
            seen_receipts.add(rid)
            if from_ms <= q["known_at_ms"] <= until_ms:
                matched = True
        proofs.append({"segment_start_ns": number,
                       "original_sha256": observed["sha256"],
                       "quote_count": len(quotes)})
        if not matched:
            continue
        original, second = sealed_original_broker_expiry(file, spec)
        if (len(original) != len(quotes)
                or second["verified_prefix_sha256"] != observed["sha256"]
                or second["original_chain_head"] != observed["chain_head"]):
            raise ContractError("PAGED_SOURCE_DOUBLE_READ_CHANGED")
        for row in original:
            at = row["available_at_ms"]
            if from_ms <= at <= until_ms:
                selected.append((
                    at, row["event_at_ms"], row["valid_until_ms"],
                    row["source_id"], _mid(row),
                ))
                if len(selected) > MAX_WINDOW_QUOTES:
                    raise ContractError("PAGED_SOURCE_WINDOW_ROW_BUDGET")
    if not sealed or not selected:
        raise ContractError("PAGED_SOURCE_WINDOW_NOT_OBSERVED")
    for old in ended_tail:
        verify_prefix(old, sealed=True)
    latest_paths, latest_numeric = _files(root)
    if latest_paths != original_paths or latest_numeric != numeric:
        raise ContractError("PAGED_SOURCE_DIRECTORY_FRONTIER_CHANGED")
    _, latest_pages = inventory(
        {"feed_directory": str(root), "period_sha256": period_sha256},
        original_paths,
    )
    if latest_pages != pages:
        raise ContractError("PAGED_SOURCE_PAGE_FRONTIER_CHANGED")
    selected.sort(key=lambda q: (q[0], q[1], q[3]))
    canonical_source = json.dumps(
        proofs, sort_keys=True, separators=(",", ":")).encode()
    fingerprint = hashlib.sha256(canonical_source).hexdigest()
    pinned_prefix = None
    if frozen_prefix_segments is not None:
        if frozen_prefix_segments > sealed:
            raise ContractError("PAGED_SOURCE_FROZEN_PREFIX_MISSING")
        # The historic FULL inventory fingerprint is preserved exactly over
        # its original first N naturally sealed numeric source records. A
        # legitimately appended successor never changes that prefix digest.
        pinned_prefix = hashlib.sha256(json.dumps(
            proofs[:frozen_prefix_segments], sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()
    return {
        "schema": SCHEMA,
        "rows": selected,
        "known_times": [r[0] for r in selected],
        "first_available_ms": selected[0][0],
        "last_available_ms": selected[-1][0],
        "source_fingerprint_sha256": fingerprint,
        "frozen_original_prefix_segments": frozen_prefix_segments,
        "frozen_original_prefix_fingerprint_sha256": pinned_prefix,
        "sealed_successors_after_frozen_prefix": (
            sealed - frozen_prefix_segments
            if frozen_prefix_segments is not None else None),
        "numeric_segments_total": len(numeric),
        "sealed_segments": sealed,
        "empty_segments": empty,
        "unsealed_newest_excluded": active_excluded,
        "verified_original_bytes": total_bytes,
        "verified_original_quote_ids": total_quotes,
        "window_quote_count": len(selected),
        "window_from_ms": from_ms,
        "window_until_ms": until_ms,
        "source_archive_page_count": len(pages),
        "source_archive_page_sha256": [
            page["sha256"] for page in pages
        ],
        "all_original_numeric_segment_proofs_verified": True,
        "not_a_future_prediction": True,
        "actual_broker_fill_or_slippage_attested": False,
        "canonical_owner_enrolled": False,
        "trade_authority": False,
        "broker_requests_sent": 0,
        "broker_orders_sent": 0,
    }
