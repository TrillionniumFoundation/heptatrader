"""SHA-verified source decoding with an optional bounded readiness view.

The full reader preserves the canonical inventory and complete quote result.
The readiness view validates sealed originals and may explicitly reuse bounded
local change-observation proofs of SHA receipts. It also reuses the earlier
successful global conflict check for identical old bytes.
Unsealed sources are always parsed; any newly overlapping content falls back
to a complete merge before a new historical conclusion is committed.
"""
from __future__ import annotations

from collections import OrderedDict
from decimal import Decimal
import hashlib
import time
from pathlib import Path

from .integrity import ContractError, canonical, digest, sha_file
from .source_archive import inventory, inventory_index, verify_prefix, prefix_version
from .shadow_source import read_source

MAX_CACHED_SEGMENTS = 320
MAX_CACHED_QUOTE_RECORDS = 180000


class _ReadinessQuoteWindow:
    """Keep every historical price conflict key, not every historical row.

    This state lasts for one strict pass only. The current view retains its
    original complete rows; only the latest old row can be returned when the
    view is empty. Earlier availability and stable segment-order ties still
    decide that row exactly as in the complete merge.
    """

    def __init__(self, earliest_ms, merge_current):
        self.earliest_ms = earliest_ms
        self.merge_current = merge_current
        self.current = {}
        self.historical_prices = {}
        self.latest_old = None

    def merge(self, records, now_ms):
        recent = []
        for quote in records:
            if quote["known_at_ms"] > now_ms:
                continue
            stamp = quote["t"]
            if stamp >= self.earliest_ms:
                recent.append(quote)
                continue
            previous = self.historical_prices.get(stamp)
            if previous is not None and (
                    Decimal(str(previous[0])) != Decimal(str(quote["bid"])) or
                    Decimal(str(previous[1])) != Decimal(str(quote["ask"]))):
                raise ContractError("CROSS_SEGMENT_PRICE_CONFLICT")
            if previous is None:
                self.historical_prices[stamp] = (quote["bid"], quote["ask"])
            old = self.latest_old
            if (old is None or stamp > old["t"] or
                    stamp == old["t"] and quote["known_at_ms"] < old["known_at_ms"]):
                self.latest_old = quote
        self.merge_current(self.current, recent, now_ms)

    def rows(self):
        if self.current:
            return [self.current[stamp] for stamp in sorted(self.current)]
        return [] if self.latest_old is None else [self.latest_old]



class _SealedOriginalProofs:
    """Bound SHA receipts to unchanged local files and a continuously held watch.

    Entries contain no source bytes. Unsupported/lost watches, file changes,
    clock regression and the bounded deadline all require raw SHA again.
    This is local change-observation evidence, not an independent signature.
    """

    def __init__(self, reverify_ms):
        from hepta_change_watch import _ChangeWatch
        self._watch = _ChangeWatch()
        self._clock = time.monotonic
        self.reverify_ms = reverify_ms
        self._entries = {}
        self._signature = None
        self._full_at = self._last_clock = self._last_wall = None
        self.full_verifications = self.full_bytes = self.reuses = self.invalidations = 0
        self.last_full_wall_ms = None

    def close(self):
        self._watch.close()
        self._entries.clear()
        self._signature = None
        self._full_at = None

    def begin(self, pins, wall_ms):
        stamp = self._clock()
        signature = tuple(digest(pin) for pin in pins)
        changed = self._watch.changed()
        regressed = ((self._last_clock is not None and stamp < self._last_clock) or
                     (self._last_wall is not None and wall_ms < self._last_wall))
        expired = self._full_at is None or not 0 <= (stamp-self._full_at)*1000 < self.reverify_ms
        self._force = (changed or regressed or expired or signature != self._signature
                       or not self._watch.available)
        if self._force:
            self.invalidations += bool(self._entries)
            self.close()
            self._signature = signature
            self._watch.arm_many([pin["path"] for pin in pins])
        self._last_clock, self._last_wall = stamp, wall_ms
        self._stamp, self._wall_ms = stamp, wall_ms
        self._pass_watched = self._watch.available
        self._pending = {}
        self._versions = {}
        self._pin_count = len(pins)
        self._full_count = 0

    def verify(self, pin):
        signature = digest(pin)
        before = prefix_version(pin, sealed=True)
        previous = self._entries.get(pin["path"])
        reused = (not self._force and self._pass_watched and
                  previous == (signature, before))
        if reused:
            self.reuses += 1
        else:
            verify_prefix(pin, sealed=True)
            self.full_verifications += 1
            self.full_bytes += pin["bytes"]
            self._full_count += 1
            if prefix_version(pin, sealed=True) != before:
                self.close()
                raise ContractError("READINESS_ORIGINAL_CHANGED_DURING_PASS")
        self._pending[pin["path"]] = (signature, before)
        self._versions[pin["path"]] = (dict(pin), before)
        return not reused

    def read_source(self, pin):
        """Decode a miss and obtain its sealed SHA from that same raw read.

        Only this proof owner invokes the verified reader; callers cannot
        supply a prior digest or assert that another read was verified. Keep
        the ordinary before/after identity and end-of-pass watch checks.
        """
        signature = digest(pin)
        before = prefix_version(pin, sealed=True)
        previous = self._entries.get(pin["path"])
        reused = (not self._force and self._pass_watched and
                  previous == (signature, before))
        parsed = read_source(Path(pin["path"]), expected_prefix=pin)
        if prefix_version(pin, sealed=True) != before:
            self.close()
            raise ContractError("READINESS_ORIGINAL_CHANGED_DURING_PASS")
        # Keep the proof-policy counters and deadline: an evicted decoder
        # entry can require a parse while its earlier SHA proof is reusable.
        if reused:
            self.reuses += 1
        else:
            self.full_verifications += 1
            self.full_bytes += pin["bytes"]
            self._full_count += 1
        self._pending[pin["path"]] = (signature, before)
        self._versions[pin["path"]] = (dict(pin), before)
        return parsed

    def finish(self):
        changed = self._watch.changed() if self._pass_watched else False
        if len(self._versions) != self._pin_count:
            self.close()
            raise ContractError("READINESS_ORIGINAL_PROOF_INCOMPLETE")
        for pin, version in self._versions.values():
            if prefix_version(pin, sealed=True) != version:
                changed = True
        # Check events again after walking identities, including overflow and
        # changes racing the last source parse or a directory replacement.
        if self._pass_watched:
            changed = self._watch.changed() or changed or not self._watch.available
        if changed:
            self.close()
            raise ContractError("READINESS_ORIGINAL_CHANGED_DURING_PASS")
        if self._pass_watched:
            self._entries = self._pending
            if self._full_count == self._pin_count:
                self._full_at = self._stamp
                self.last_full_wall_ms = self._wall_ms
        else:
            self._entries.clear()
            self._full_at = None

    def counters(self):
        return {
            "original_proof_policy": "SHA256_WITH_CONTINUOUS_LOCAL_CHANGE_OBSERVATION",
            "original_proof_reverify_ms": self.reverify_ms,
            "original_proof_watch_available": self._watch.available,
            "original_proof_entries": len(self._entries),
            "original_proof_full_sha256_verifications": self.full_verifications,
            "original_proof_full_sha256_bytes": self.full_bytes,
            "original_proof_reuses": self.reuses,
            "original_proof_invalidations": self.invalidations,
            "original_proof_last_full_at_ms": self.last_full_wall_ms,
        }


class VerifiedSealedReadinessCache:
    """One process and frozen config; no independently signed source claims."""

    def __init__(self, *, original_proof_reverify_ms=0):
        if (type(original_proof_reverify_ms) is not int or
                original_proof_reverify_ms and not 60000 <= original_proof_reverify_ms <= 3600000):
            raise ContractError("READINESS_ORIGINAL_PROOF_INTERVAL")
        self._proofs = _SealedOriginalProofs(original_proof_reverify_ms) if original_proof_reverify_ms else None
        self._proof_pass = False
        self._config_digest = None
        self._cached = OrderedDict()
        self._cached_records = 0
        self._history = None
        self.sealed_hits = 0
        self.sealed_hits_bytes_reverified_at_use = 0
        self.sealed_misses = 0
        self.unsealed_reparsed = 0
        self.archive_inventory_verified = 0
        self.archive_index_verified = 0
        self.history_reused = 0
        self.history_fallbacks = 0
        self.retired_segments_reverified = 0
        self.last_merge_historical_keys = 0
        self.last_merge_full_rows = 0

    def close(self):
        if self._proofs is not None:
            self._proofs.close()

    def _verify_original(self, pin):
        if self._proof_pass:
            return self._proofs.verify(pin)
        verify_prefix(pin, sealed=True)
        return True

    def _trim(self):
        while (len(self._cached) > MAX_CACHED_SEGMENTS or
               self._cached_records > MAX_CACHED_QUOTE_RECORDS):
            _, (_, records, _, _) = self._cached.popitem(last=False)
            self._cached_records -= len(records)

    def _segment(self, config, path, pin, index, cache_floor, wall_ms,
                 *, inventory_verified):
        key = str(path)
        entry = self._cached.get(key) if pin is not None else None
        if entry is not None and entry[2] == pin:
            if self._verify_original(pin):
                self.sealed_hits_bytes_reverified_at_use += 1
            header, records, prefix, accepted_at_ms = entry
            if wall_ms < accepted_at_ms:
                raise ContractError("READINESS_CACHE_CLOCK_REGRESSED")
            self._cached.move_to_end(key)
            self.sealed_hits += 1
        else:
            # The index is not proof of original bytes. A miss keeps the
            # sealed size/END/path checks, then binds the strict parse receipt
            # to the very same pin, including any uncommitted tail.
            if pin is not None and not inventory_verified:
                if self._proof_pass:
                    header, records, prefix = self._proofs.read_source(pin)
                else:
                    header, records, prefix = read_source(path, expected_prefix=pin)
            else:
                header, records, prefix = read_source(path)
            if header["period_sha256"] != config["period_sha256"]:
                raise ContractError("CROSS_PERIOD_FEED")
            if pin is not None and prefix != pin:
                raise ContractError("SOURCE_ARCHIVE_PREFIX_CHANGED")
            accepted_at_ms = int(time.time() * 1000)
            if pin is not None:
                self.sealed_misses += 1
                if index >= cache_floor:
                    old = self._cached.pop(key, None)
                    if old is not None:
                        self._cached_records -= len(old[1])
                    self._cached[key] = (header, records, dict(prefix), accepted_at_ms)
                    self._cached_records += len(records)
                    self._trim()
            else:
                self.unsealed_reparsed += 1
        if header["period_sha256"] != config["period_sha256"]:
            raise ContractError("CROSS_PERIOD_FEED")
        if pin is not None and prefix != pin:
            raise ContractError("SOURCE_ARCHIVE_PREFIX_CHANGED")
        return records, prefix, accepted_at_ms

    @staticmethod
    def _summary(records, prefix, accepted_at_ms, *, historical_cutoff=None):
        # Include every strictly parsed row, even one not yet visible as of
        # this assessment. A previous visible subset is not a future proof.
        latest = max(records, key=lambda q: (q["t"], -q["known_at_ms"]), default=None)
        overlap = None
        if historical_cutoff is not None:
            checksum = hashlib.sha256()
            count = 0
            max_known = 0
            for quote in records:
                if quote["t"] <= historical_cutoff:
                    # Preserve the complete parser output, including receipt
                    # and order. No additional deduplication or old row cache.
                    checksum.update(canonical(quote) + b"\n")
                    count += 1
                    max_known = max(max_known, quote["known_at_ms"])
            overlap = {"cutoff": historical_cutoff, "count": count,
                       "max_known_at_ms": max_known, "sha256": checksum.hexdigest()}
        return {
            "latest_quote": dict(latest) if latest is not None else None,
            "prefix": dict(prefix),
            "min_t": min((q["t"] for q in records), default=None),
            "max_t": max((q["t"] for q in records), default=None),
            "max_known_at_ms": max((q["known_at_ms"] for q in records), default=0),
            "accepted_at_ms": accepted_at_ms,
            "historical_overlap": overlap,
        }

    def _reusable_history(self, sealed, feed_names, now_ms, earliest_ms, wall_ms):
        old = self._history
        if old is None or earliest_ms is None:
            return None
        old_names = set(old["feed_paths"])
        retained_order = tuple(name for name in feed_names if name in old_names)
        # A long-lived recovery directory can sort after each newly rotated
        # tail. Preserve all old sources in order; new sources are still fully
        # parsed below and any overlap with retired history forces a full merge.
        if (tuple(sealed) != old["sealed"] or
                retained_order != old["feed_paths"] or
                now_ms < old["asof_ms"] or earliest_ms < old["earliest_ms"]):
            self.history_fallbacks += 1
            self._history = None
            return None
        if wall_ms < old["accepted_at_ms"]:
            raise ContractError("READINESS_CACHE_CLOCK_REGRESSED")
        return old

    @staticmethod
    def _merge(merged, records, now_ms):
        for q in records:
            if q["known_at_ms"] > now_ms:
                continue
            old = merged.get(q["t"])
            if old and (Decimal(str(old["bid"])) != Decimal(str(q["bid"])) or
                        Decimal(str(old["ask"])) != Decimal(str(q["ask"]))):
                raise ContractError("CROSS_SEGMENT_PRICE_CONFLICT")
            if old is None or q["known_at_ms"] < old["known_at_ms"]:
                merged[q["t"]] = q

    @staticmethod
    def _retired_sources(sealed, summaries, now_ms, earliest_ms):
        return frozenset(
            pin["path"] for pin in sealed
            if (summaries[pin["path"]]["max_t"] is None or
                summaries[pin["path"]]["max_t"] < earliest_ms)
            and summaries[pin["path"]]["max_known_at_ms"] <= now_ms
        )

    def _commit_history(self, previous, sealed, feed_names, summaries,
                        now_ms, earliest_ms):
        if previous is None:
            retired = self._retired_sources(sealed, summaries, now_ms, earliest_ms)
            if not retired:
                self._history = None
                return
            latest_t = max((summaries[name]["max_t"] for name in retired
                            if summaries[name]["max_t"] is not None), default=None)
            accepted_at_ms = max(summaries[name]["accepted_at_ms"] for name in retired)
        else:
            # A newly sealed page requires a full pass. Within an unchanged
            # sealed inventory keep the retired boundary fixed and simple.
            retired = previous["retired"]
            latest_t = previous["latest_t"]
            accepted_at_ms = previous["accepted_at_ms"]
            self.history_reused += 1
        self._history = {
            "sealed": tuple(dict(pin) for pin in sealed),
            "feed_paths": feed_names,
            "retired": retired,
            "latest_t": latest_t,
            "accepted_at_ms": accepted_at_ms,
            "sources": summaries,
            "asof_ms": now_ms,
            "earliest_ms": earliest_ms,
        }
        # The historical conclusion stores no old quote population. Retain
        # the existing decoded-record cap for sources still needed in full.
        for name in retired:
            entry = self._cached.pop(name, None)
            if entry is not None:
                self._cached_records -= len(entry[1])

    def read_quotes(self, config, now_ms, *, earliest_ms=None):
        """Full quotes by default; optional recent view plus the latest quote.

        The extra last quote preserves exact stale age even when all quotes
        precede the requested view. Cross-history conflicts remain checked.
        """
        if type(now_ms) is not int or now_ms <= 0:
            raise ContractError("READINESS_CACHE_CLOCK_INVALID")
        if earliest_ms is not None and (type(earliest_ms) is not int or earliest_ms > now_ms):
            raise ContractError("READINESS_CACHE_WINDOW_INVALID")
        signature = digest(config)
        if self._config_digest is None:
            self._config_digest = signature
        elif signature != self._config_digest:
            raise ContractError("READINESS_CACHE_CONFIG_CHANGED")
        if sha_file(Path(config["period_path"])) != config["period_sha256"]:
            raise ContractError("RESIDENT_PERIOD_CHANGED")
        feed_paths = sorted(Path(config["feed_directory"]).glob("segment-*/quotes.jsonl"))
        if earliest_ms is None:
            sealed, pages = inventory(config, feed_paths)
            self.archive_inventory_verified += 1
        else:
            # This only verifies the complete page ledger and inventory.
            # Every original is verified below immediately before consuming
            # its decoded data or its prior successful semantic conclusion.
            sealed, pages = inventory_index(config, feed_paths)
            self.archive_index_verified += 1
        extra = [Path(x) for x in config.get("completed_history_sources", [])]
        if len(extra) > 128:
            raise ContractError("COMPLETED_PROBE_SOURCE_BOUND")
        paths = list(dict.fromkeys(feed_paths + extra))
        if len(paths) != len(feed_paths) + len(extra):
            raise ContractError("DUPLICATE_SOURCE_PATH")
        sealed_names = {str(p) for p in feed_paths[:len(sealed)]}
        for name in list(self._cached):
            if name not in sealed_names:
                _, records, _, _ = self._cached.pop(name)
                self._cached_records -= len(records)
        sealed_indexes = {str(p): i for i, p in enumerate(feed_paths[:len(sealed)])}
        cache_floor = min((sealed_indexes[name] for name in self._cached), default=0)
        feed_names = tuple(str(p) for p in feed_paths)
        wall_ms = int(time.time() * 1000)
        history = self._reusable_history(sealed, feed_names, now_ms, earliest_ms, wall_ms)
        self._proof_pass = self._proofs is not None and earliest_ms is not None
        if self._proof_pass:
            self._proofs.begin(sealed, wall_ms)
        merged = {}
        window = _ReadinessQuoteWindow(earliest_ms, self._merge) if earliest_ms is not None else None
        def merge(records):
            if window is None:
                self._merge(merged, records, now_ms)
            else:
                window.merge(records, now_ms)
        summaries = {}
        prefixes = list(pages)
        historical_cutoff = history["latest_t"] if history is not None else None
        for index, path in enumerate(paths):
            key = str(path)
            pin = sealed[index] if index < len(sealed) else None
            if history is not None and key in history["retired"]:
                if self._verify_original(pin):
                    self.retired_segments_reverified += 1
                summaries[key] = history["sources"][key]
                boundary = summaries[key]["latest_quote"]
                if boundary is not None and boundary["t"] == history["latest_t"]:
                    merge([boundary])
                continue
            records, prefix, accepted_at_ms = self._segment(
                config, path, pin, index, cache_floor, wall_ms,
                inventory_verified=earliest_ms is None)
            if pin is None:
                prefixes.append(prefix)
            if earliest_ms is not None:
                if pin is None and index == len(sealed) and history is None:
                    retired = self._retired_sources(sealed, summaries, now_ms, earliest_ms)
                    historical_cutoff = max(
                        (summaries[name]["max_t"] for name in retired
                         if summaries[name]["max_t"] is not None), default=None)
                summary = self._summary(
                    records, prefix, accepted_at_ms,
                    historical_cutoff=historical_cutoff if pin is None else None)
                summaries[key] = summary
                if (history is not None and history["latest_t"] is not None and
                        summary["min_t"] is not None and
                        summary["min_t"] <= history["latest_t"]):
                    before = history["sources"].get(key)
                    overlap = summary["historical_overlap"]
                    unchanged_overlap = (
                        pin is None and before is not None and overlap is not None and
                        overlap == before.get("historical_overlap") and
                        overlap["max_known_at_ms"] <= history["asof_ms"])
                    if not unchanged_overlap and (
                            before is None or prefix != before["prefix"] or
                            before["max_known_at_ms"] > history["asof_ms"]):
                        # A strict full parse may prove that an append or END
                        # changed no old quote. Otherwise an old receipt alone
                        # cannot rule out a late cross-history conflict.
                        self._history = None
                        self.history_fallbacks += 1
                        return self.read_quotes(config, now_ms, earliest_ms=earliest_ms)
                if (pin is not None and
                        (summary["max_t"] is None or summary["max_t"] < earliest_ms) and
                        summary["max_known_at_ms"] <= now_ms):
                    # Cache eviction grants no historical proof. This pass
                    # still checks every old price against every other source,
                    # and history is committed only after the original checks.
                    entry = self._cached.pop(key, None)
                    if entry is not None:
                        self._cached_records -= len(entry[1])
            merge(records)
        rows = window.rows() if window is not None else [merged[t] for t in sorted(merged)]
        result = [
            {**row, "bid": float(row["bid"]), "ask": float(row["ask"])}
            for row in rows
        ]
        if self._proof_pass:
            self._proofs.finish()
        if earliest_ms is not None:
            self._commit_history(history, sealed, feed_names, summaries,
                                 now_ms, earliest_ms)
        self.last_merge_historical_keys = len(window.historical_prices) if window is not None else 0
        self.last_merge_full_rows = (len(window.current) + int(window.latest_old is not None)
                                    if window is not None else len(merged))
        return result, prefixes

    def counters(self):
        return {
            **(self._proofs.counters() if self._proofs is not None else {}),
            "archive_inventories_full_sha256_reverified": self.archive_inventory_verified,
            "archive_indexes_reverified": self.archive_index_verified,
            "sealed_decoding_reused": self.sealed_hits,
            "sealed_hits_bytes_reverified_at_use": self.sealed_hits_bytes_reverified_at_use,
            "sealed_decoding_strictly_parsed": self.sealed_misses,
            "unsealed_or_extra_strictly_reparsed": self.unsealed_reparsed,
            "historical_semantic_passes_reused": self.history_reused,
            "historical_semantic_full_fallbacks": self.history_fallbacks,
            "retired_original_segments_sha256_reverified": self.retired_segments_reverified,
            "retired_decoded_segments": len(self._history["retired"]) if self._history else 0,
            "cached_sealed_segments": len(self._cached),
            "cached_original_quote_records": self._cached_records,
            "last_merge_historical_price_keys": self.last_merge_historical_keys,
            "last_merge_complete_quote_rows": self.last_merge_full_rows,
            "cache_is_not_independent_broker_signature": True,
            "trade_authority": False,
            "broker_orders_sent": 0,
        }
