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
from .shadow_source import read_source, strict
from .source_archive import inventory, verify_prefix

MAX_FOOTER_BYTES = 65536
MIN_CAUSAL_SPAN_MS = 360000
MIN_OUTCOME_SPAN_MS = 420000


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
    archived, pages = inventory(config, paths)
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
    # Every sealed original prefix was rehashed by inventory(); its
    # verified END event places a strict upper bound on ALL archived quotes.
    # The recovery-live unsealed segment may span earlier dates and overlap
    # archived input. Only a RECENT as-of window is eligible here.
    for pin in archived:
        if _sealed_end_timestamp(pin) >= cutoff:
            raise ContractError("RECENT_SOURCE_CANNOT_SKIP_RECENT_ARCHIVE")
    # A concurrent archive rotation cannot silently change the pin
    # population used to build this new as-of decision.
    after_paths = sorted(feed.glob("segment-*/quotes.jsonl"))
    after_archived, after_pages = inventory(config, after_paths)
    if paths != after_paths or archived != after_archived or pages != after_pages:
        raise ContractError("RECENT_SOURCE_ARCHIVE_CHANGED_DURING_READ")
    # Recheck all originally sealed bytes after collecting the tail:
    # a changed historical source cannot produce a "fast" success.
    for pin in archived:
        verify_prefix(pin, sealed=True)
    ordered = [
        {**quotes[t], "bid": float(quotes[t]["bid"]),
         "ask": float(quotes[t]["ask"])} for t in sorted(quotes)
    ]
    return ordered, prefixes
