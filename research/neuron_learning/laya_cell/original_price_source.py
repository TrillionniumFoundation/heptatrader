"""A bounded original-quote window with complete canonical archive verification.

Old sealed segments can skip JSON decoding only after canonical inventory
rehashes their bytes and their original END precedes the entire input window.
Unsealed segments are always strictly parsed. No broker or source writes.
"""
from __future__ import annotations
from decimal import Decimal
from pathlib import Path
from .integrity import ContractError, sha_file
from .shadow_source import read_source
from .source_archive import inventory, verify_prefix
from .resident_recent_source import _sealed_end_timestamp

WINDOW_MS = 4500000
MAX_SELECTED_QUOTES = 100000

def read_original_price_window(config, asof_ms):
    if type(asof_ms) is not int or asof_ms <= WINDOW_MS:
        raise ContractError("ORIGINAL_PRICE_SOURCE_CLOCK")
    if config.get("completed_history_sources"):
        raise ContractError("ORIGINAL_PRICE_EXTRA_HISTORY_FORBIDDEN")
    if sha_file(Path(config["period_path"])) != config["period_sha256"]:
        raise ContractError("RESIDENT_PERIOD_CHANGED")
    paths = sorted(Path(config["feed_directory"]).glob("segment-*/quotes.jsonl"))
    sealed, pages = inventory(config, paths)
    cutoff = asof_ms-WINDOW_MS
    merged = {}
    used = []
    skipped = 0
    for i, path in enumerate(paths):
        pin = sealed[i] if i < len(sealed) else None
        if pin is not None and _sealed_end_timestamp(pin) < cutoff:
            skipped += 1
            continue
        header, records, prefix = read_source(path)
        if header["period_sha256"] != config["period_sha256"]:
            raise ContractError("CROSS_PERIOD_FEED")
        if pin is not None and prefix != pin:
            raise ContractError("SOURCE_ARCHIVE_PREFIX_CHANGED")
        used.append(prefix)
        for q in records:
            if q["known_at_ms"]>asof_ms or q["t"]>asof_ms or q["t"]<cutoff:
                continue
            old = merged.get(q["t"])
            if old and any(Decimal(str(old[k])) != Decimal(str(q[k])) for k in ("bid","ask")):
                raise ContractError("CROSS_SEGMENT_PRICE_CONFLICT")
            if old is None or q["known_at_ms"]<old["known_at_ms"]:
                merged[q["t"]] = q
            if len(merged)>MAX_SELECTED_QUOTES:
                raise ContractError("ORIGINAL_PRICE_SOURCE_CAPACITY")
    after_paths = sorted(Path(config["feed_directory"]).glob("segment-*/quotes.jsonl"))
    after_sealed, after_pages = inventory(config, after_paths)
    if paths != after_paths or sealed != after_sealed or pages != after_pages:
        raise ContractError("ORIGINAL_PRICE_ARCHIVE_CHANGED_DURING_READ")
    for pin in sealed:
        verify_prefix(pin, sealed=True)
    if sha_file(Path(config["period_path"])) != config["period_sha256"]:
        raise ContractError("RESIDENT_PERIOD_CHANGED")
    quotes = [{**merged[t], "bid":float(merged[t]["bid"]),
               "ask":float(merged[t]["ask"])} for t in sorted(merged)]
    proof = {"schema":"heptatrader.original-price-source-window.v1",
             "asof_ms":asof_ms, "cutoff_ms":cutoff,
             "archive_pages":pages, "selected_original_prefixes":used,
             "old_sealed_segments_fully_rehashed_not_decoded":skipped,
             "source_identity":"ORIGINAL_VERIFIED_PAPER_QUOTES",
             "period_sha256":config["period_sha256"],
             "quote_count":len(quotes), "trade_authority":False}
    return quotes, proof
