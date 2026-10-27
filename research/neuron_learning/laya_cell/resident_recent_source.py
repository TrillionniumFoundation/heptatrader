"""Opt-in bounded original PAPER quote reader for FUTURE Laya shadow trials.

Current frozen forward plans keep the full historical reader. This path
retains every archived byte/ledger check, skips parsing only proven older
sealed segments and never upgrades economic/model authority.
"""
from __future__ import annotations
from decimal import Decimal
import os
import stat
from pathlib import Path

from .integrity import ContractError, sha_file
from .shadow_source import read_source, strict, digest as source_digest
from .source_archive import inventory_index, prefix_version, verify_prefix

MAX_FOOTER_BYTES = 65536
MIN_CAUSAL_SPAN_MS = 360000
MIN_OUTCOME_SPAN_MS = 420000


def _archive_snapshot(config, paths):
    """Capture the checked ledger and file versions; these are not SHA proof.

    The final full hash remains mandatory. Any END used before that boundary
    is authenticated against its pinned chain head, and parsed sealed rows
    are bound to the original SHA by read_source(expected_prefix=...).
    """
    archived, pages = inventory_index(config, paths)
    versions = [prefix_version(pin, sealed=True) for pin in archived]
    return archived, pages, versions


def _archive_unchanged(config, paths, archived, pages, versions, reason):
    after_paths = sorted(Path(config["feed_directory"]).glob("segment-*/quotes.jsonl"))
    after_archived, after_pages = inventory_index(config, after_paths)
    if paths != after_paths or archived != after_archived or pages != after_pages:
        raise ContractError(reason)
    for pin, version in zip(archived, versions):
        if prefix_version(pin, sealed=True) != version:
            raise ContractError("RECENT_SOURCE_ORIGINAL_CHANGED_DURING_READ")
        # Keep the full final byte check: an already writable mmap can alter
        # a dirty page without updating the stat tuple. No stat-only reuse.
        verify_prefix(pin, sealed=True)
        if prefix_version(pin, sealed=True) != version:
            raise ContractError("RECENT_SOURCE_ORIGINAL_CHANGED_DURING_READ")


def _sealed_end_timestamp(pin):
    """Check the archive's *unchanged* original END, not an invented index."""
    path = Path(pin["path"])
    length = pin.get("bytes")
    if (type(length) is not int or length <= 0
            or pin.get("ended") is not True
            or pin.get("uncommitted_tail_bytes") != 0
            or not isinstance(pin.get("chain_head"), str)):
        raise ContractError("RECENT_SEALED_SOURCE_END_UNPROVEN")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid()
                or st.st_mode & 0o077 or st.st_size != length):
            raise ContractError("RECENT_SEALED_SOURCE_CHANGED")
        size = min(length, MAX_FOOTER_BYTES)
        os.lseek(fd, length - size, os.SEEK_SET)
        tail = os.read(fd, size)
    finally:
        os.close(fd)
    if not tail.endswith(b"\n"):
        raise ContractError("RECENT_SEALED_SOURCE_UNCOMMITTED_TAIL")
    end = strict(tail.splitlines()[-1])
    if (end.get("kind") != "END"
            or end.get("sha256") != pin["chain_head"]
            or source_digest({k:v for k,v in end.items() if k != "sha256"}) != pin["chain_head"]
            or type(end.get("at_ms")) is not int
            or end["at_ms"] <= 0):
        raise ContractError("RECENT_SEALED_SOURCE_END_CHANGED")
    return end["at_ms"]


def read_recent_original_quotes(config, now_ms, *, min_span_ms=MIN_CAUSAL_SPAN_MS):
    """Return a recent contiguous source population with COMPLETE archive pins.

    Every sealed source END must precede the entire relevant recent
    decision window. The unsealed recovery-live tail can overlap much
    older archives; we do not assume its earliest quote is chronologically
    after all archived quotes. Missing recent coverage remains HOLD.
    """
    if (type(now_ms) is not int or now_ms <= 0
            or type(min_span_ms) is not int
            or min_span_ms not in (MIN_CAUSAL_SPAN_MS, MIN_OUTCOME_SPAN_MS)):
        raise ContractError("RECENT_SOURCE_WINDOW_NOT_REGISTERED")
    if config.get("completed_history_sources"):
        raise ContractError("RECENT_SOURCE_MIXED_HISTORY_NOT_SUPPORTED")
    if sha_file(Path(config["period_path"])) != config["period_sha256"]:
        raise ContractError("RESIDENT_PERIOD_CHANGED")
    feed = Path(config["feed_directory"])
    paths = sorted(feed.glob("segment-*/quotes.jsonl"))
    archived, pages, versions = _archive_snapshot(config, paths)
    if len(archived) >= len(paths):
        raise ContractError("RECENT_SOURCE_NO_UNSEALED_TAIL")
    unsealed = paths[len(archived):]
    quotes = {}
    prefixes = list(pages)
    for path in unsealed:
        header, records, prefix = read_source(path)
        if header.get("period_sha256") != config["period_sha256"]:
            raise ContractError("CROSS_PERIOD_FEED")
        prefixes.append(prefix)
        for quote in records:
            if quote["known_at_ms"] > now_ms:
                continue
            old = quotes.get(quote["t"])
            if old and (Decimal(str(old["bid"])) != Decimal(str(quote["bid"]))
                        or Decimal(str(old["ask"])) != Decimal(str(quote["ask"]))):
                raise ContractError("CROSS_SEGMENT_PRICE_CONFLICT")
            if old is None or quote["known_at_ms"] < old["known_at_ms"]:
                quotes[quote["t"]] = quote
    if len(quotes) < 6:
        raise ContractError("RECENT_SOURCE_INSUFFICIENT_EVIDENCE")
    first_event = min(quotes)
    latest_event = max(quotes)
    cutoff = now_ms - min_span_ms
    if first_event > cutoff:
        raise ContractError("RECENT_SOURCE_INSUFFICIENT_HORIZON_COVERAGE")
    if latest_event > now_ms:
        raise ContractError("RECENT_SOURCE_FUTURE_EVENT")
    # The END's own digest is bound to the archive's pinned chain head. It
    # places a strict upper bound on ALL original quotes. Every sealed byte
    # still passes the full SHA check before this function returns.
    # The recovery-live unsealed segment may span earlier dates and overlap
    # archived input. Only a RECENT as-of window is eligible here.
    for pin in archived:
        if _sealed_end_timestamp(pin) >= cutoff:
            raise ContractError("RECENT_SOURCE_CANNOT_SKIP_RECENT_ARCHIVE")
    # A concurrent archive rotation cannot silently change the pin
    # population used to build this new as-of decision.
    _archive_unchanged(config, paths, archived, pages, versions,
                       "RECENT_SOURCE_ARCHIVE_CHANGED_DURING_READ")
    ordered = [
        {**quotes[t], "bid": float(quotes[t]["bid"]),
         "ask": float(quotes[t]["ask"])} for t in sorted(quotes)
    ]
    return ordered, prefixes


def read_original_quote_range(config, now_ms, first_ms, last_ms):
    '''Read a pending outcome range plus its first later known quote.

    The successor preserves ENDPOINT_GAP versus OUTCOME_MISSING and grace.
    A sealed END bounds every quote: observed_at <= QUOTE.at <= END.at.
    Original archive bytes pass a fresh SHA check; their exact file versions
    must remain unchanged throughout this projection.
    '''
    if (any(type(value) is not int for value in (now_ms, first_ms, last_ms))
            or not 0 < first_ms <= last_ms <= now_ms):
        raise ContractError('PENDING_OUTCOME_SOURCE_INVALID_RANGE')
    if config.get('completed_history_sources'):
        raise ContractError('PENDING_OUTCOME_SOURCE_COMPLETED_PROBES_UNSUPPORTED')
    if sha_file(Path(config['period_path'])) != config['period_sha256']:
        raise ContractError('RESIDENT_PERIOD_CHANGED')
    feed = Path(config['feed_directory'])
    paths = sorted(feed.glob('segment-*/quotes.jsonl'))
    archived, pages, versions = _archive_snapshot(config, paths)
    quotes = {}
    prefixes = list(pages)
    successor_ms = None
    for index, path in enumerate(paths):
        if index < len(archived) and _sealed_end_timestamp(archived[index]) < first_ms:
            continue
        header, records, pin = (read_source(path, expected_prefix=archived[index])
                                if index < len(archived) else read_source(path))
        if header['period_sha256'] != config['period_sha256']:
            raise ContractError('CROSS_PERIOD_FEED')
        if index < len(archived):
            if pin != archived[index]:
                raise ContractError('SOURCE_ARCHIVE_PREFIX_CHANGED')
        else:
            prefixes.append(pin)
        for quote in records:
            stamp = quote['t']
            if quote['known_at_ms'] > now_ms or stamp < first_ms:
                continue
            if stamp > last_ms:
                if successor_ms is not None and stamp > successor_ms:
                    continue
                if successor_ms is not None and stamp < successor_ms:
                    quotes.pop(successor_ms, None)
                successor_ms = stamp
            previous = quotes.get(stamp)
            if previous and (
                    Decimal(str(previous['bid'])) != Decimal(str(quote['bid']))
                    or Decimal(str(previous['ask'])) != Decimal(str(quote['ask']))):
                raise ContractError('CROSS_SEGMENT_PRICE_CONFLICT')
            if previous is None or quote['known_at_ms'] < previous['known_at_ms']:
                quotes[stamp] = quote
    _archive_unchanged(config, paths, archived, pages, versions,
                       'PENDING_OUTCOME_SOURCE_ARCHIVE_CHANGED_DURING_READ')
    ordered = [
        {**quotes[stamp], 'bid': float(quotes[stamp]['bid']),
         'ask': float(quotes[stamp]['ask'])}
        for stamp in sorted(quotes)
    ]
    return ordered, prefixes
