"""Opt-in sandbox fast-source reader: cache only EXACTLY SHA-verified sealed pages.

The canonical source_archive.inventory() is run on EVERY read. It checks
the complete page ledger, full original sealed-file bytes and SHA256.
Only segments whose digest was verified by that invocation may reuse a
previous strict read_source() parse, never caller-supplied file metadata.
Unsealed segments, current tails and any additional source are re-parsed.
No IB client, source write, native model, or permission to trade.
"""
from __future__ import annotations

from collections import OrderedDict
from decimal import Decimal
import os
import time
from pathlib import Path

from .integrity import ContractError, digest, sha_file
from .source_archive import inventory, verify_prefix
from .shadow_source import read_source

MAX_CACHED_SEGMENTS = 320
MAX_CACHED_QUOTE_RECORDS = 180000

class VerifiedSealedReadinessCache:
    """One process, one frozen source config, no independently signed claims."""

    def __init__(self):
        self._config_digest = None
        self._cached = OrderedDict()
        self._cached_records = 0
        self.sealed_hits = 0
        self.sealed_hits_bytes_reverified_at_use = 0
        self.sealed_misses = 0
        self.unsealed_reparsed = 0
        self.archive_inventory_verified = 0

    def _trim(self):
        while (len(self._cached) > MAX_CACHED_SEGMENTS or
               self._cached_records > MAX_CACHED_QUOTE_RECORDS):
            _, (_, records, _, _) = self._cached.popitem(last=False)
            self._cached_records -= len(records)

    def read_quotes(self, config, now_ms):
        if type(now_ms) is not int or now_ms <= 0:
            raise ContractError("READINESS_CACHE_CLOCK_INVALID")
        signature = digest(config)
        if self._config_digest is None:
            self._config_digest = signature
        elif signature != self._config_digest:
            raise ContractError("READINESS_CACHE_CONFIG_CHANGED")

        # Preserve canonical first gate, not optimistic checks from a cache.
        if sha_file(Path(config["period_path"])) != config["period_sha256"]:
            raise ContractError("RESIDENT_PERIOD_CHANGED")

        feed_paths = sorted(Path(config["feed_directory"]).glob(
            "segment-*/quotes.jsonl"))
        # Canonical inventory hashes EVERY immutable segment's full original
        # bytes with O_NOFOLLOW and re-verifies the complete archive ledger.
        sealed, pages = inventory(config, feed_paths)
        self.archive_inventory_verified += 1

        extra = [Path(x) for x in config.get("completed_history_sources", [])]
        if len(extra) > 128:
            raise ContractError("COMPLETED_PROBE_SOURCE_BOUND")
        paths = list(dict.fromkeys(feed_paths + extra))
        if len(paths) != len(feed_paths) + len(extra):
            raise ContractError("DUPLICATE_SOURCE_PATH")

        # Discard caches that are no longer in the full current discovery set.
        sealed_names = {str(p) for p in feed_paths[:len(sealed)]}
        for name in list(self._cached):
            if name not in sealed_names:
                _, stored, _, _ = self._cached.pop(name)
                self._cached_records -= len(stored)

        # The canonical inventory is in ascending segment order. If that
        # inventory is larger than either cache bound, caching an older miss
        # ahead of our retained suffix would evict the NEXT still-cached
        # segment; by the end of the scan this repeats for every segment and
        # turns the cache into 0 hits. Preserve the verified recent suffix:
        # earlier uncached segments are still strictly read on EVERY pass.
        sealed_indexes = {str(path): i for i,path in enumerate(feed_paths[:len(sealed)])}
        cache_floor = min((sealed_indexes[name] for name in self._cached), default=0)

        merged = {}
        prefixes = list(pages)
        wall_ms = int(time.time() * 1000)
        for index, path in enumerate(paths):
            key = str(path)
            pin = sealed[index] if index < len(sealed) else None
            entry = self._cached.get(key) if pin is not None else None
            if entry is not None and entry[2] == pin:
                # Prevent use of an archived file swapped AFTER the initial
                # inventory pass. Hash original exact bytes again at use.
                verify_prefix(pin, sealed=True)
                self.sealed_hits_bytes_reverified_at_use += 1
                header, records, prefix, accepted_at_ms = entry
                # A backward wall clock would make canonical read_source()
                # reject the old END event. It must fail closed here too.
                if wall_ms < accepted_at_ms:
                    raise ContractError("READINESS_CACHE_CLOCK_REGRESSED")
                self._cached.move_to_end(key)
                self.sealed_hits += 1
            else:
                header, records, prefix = read_source(path)
                if header["period_sha256"] != config["period_sha256"]:
                    raise ContractError("CROSS_PERIOD_FEED")
                if pin is not None and prefix != pin:
                    raise ContractError("SOURCE_ARCHIVE_PREFIX_CHANGED")
                if pin is not None:
                    self.sealed_misses += 1
                    if index >= cache_floor:
                        old = self._cached.pop(key, None)
                        if old is not None:
                            self._cached_records -= len(old[1])
                        self._cached[key] = (header, records, prefix, wall_ms)
                        self._cached_records += len(records)
                        self._trim()
                else:
                    self.unsealed_reparsed += 1
            # Keep the same per-segment checks for both cache-hit and miss.
            if header["period_sha256"] != config["period_sha256"]:
                raise ContractError("CROSS_PERIOD_FEED")
            if pin is not None:
                if prefix != pin:
                    raise ContractError("SOURCE_ARCHIVE_PREFIX_CHANGED")
            else:
                prefixes.append(prefix)
            for q in records:
                if q["known_at_ms"] > now_ms:
                    continue
                old = merged.get(q["t"])
                if old and (Decimal(str(old["bid"])) !=
                            Decimal(str(q["bid"])) or
                            Decimal(str(old["ask"])) !=
                            Decimal(str(q["ask"]))):
                    raise ContractError("CROSS_SEGMENT_PRICE_CONFLICT")
                if old is None or q["known_at_ms"] < old["known_at_ms"]:
                    merged[q["t"]] = q

        ordered = [
            {**merged[t], "bid": float(merged[t]["bid"]),
             "ask": float(merged[t]["ask"])}
            for t in sorted(merged)
        ]
        return ordered, prefixes

    def counters(self):
        return {
            "archive_inventories_full_sha256_reverified":
                self.archive_inventory_verified,
            "sealed_decoding_reused": self.sealed_hits,
            "sealed_hits_bytes_reverified_at_use":
                self.sealed_hits_bytes_reverified_at_use,
            "sealed_decoding_strictly_parsed": self.sealed_misses,
            "unsealed_or_extra_strictly_reparsed": self.unsealed_reparsed,
            "cached_sealed_segments": len(self._cached),
            "cached_original_quote_records": self._cached_records,
            "cache_is_not_independent_broker_signature": True,
            "trade_authority": False,
            "broker_orders_sent": 0,
        }
