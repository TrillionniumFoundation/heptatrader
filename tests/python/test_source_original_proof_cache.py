"""Changed originals and lost observation invalidate local sealed SHA reuse."""
import hashlib
import errno
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from laya_cell.integrity import ContractError
from laya_cell.verified_sealed_readiness_cache import VerifiedSealedReadinessCache
from laya_cell import verified_sealed_readiness_cache as module
from laya_cell import shadow_source
from laya_cell import source_archive
from laya_cell.integrity import canonical, digest, sha_file
from laya_cell.resident_data import read_quotes
from laya_cell.source_archive import inventory_index, seal
from hepta_change_watch import _ChangeWatch
from test_verified_sealed_readiness_cache import RecentHarness


class OriginalProofTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.h = RecentHarness(tmp.name)
        self.cache = VerifiedSealedReadinessCache(original_proof_reverify_ms=60000)
        self.addCleanup(self.cache.close)
        self.context = self.h.patched()
        self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.clock = [100.0]
        self.cache._proofs._clock = lambda: self.clock[0]

    def read(self):
        return self.cache.read_quotes(self.h.config, 3000, earliest_ms=1500)

    def cold(self):
        result = self.read()
        if not self.cache._proofs._watch.available:
            self.skipTest("continuous file change observation unavailable")
        return result

    def test_unchanged_retired_original_reuses_sha_and_preserves_result(self):
        with patch.object(module, "verify_prefix", wraps=module.verify_prefix) as verify:
            cold = self.cold()
            count = verify.call_count
            self.assertEqual(self.read(), cold)
            self.assertEqual(verify.call_count, count)
        stats = self.cache.counters()
        self.assertEqual(stats["original_proof_reuses"], 1)
        self.assertEqual(stats["original_proof_full_sha256_verifications"], 1)
        self.assertEqual(stats["cached_original_quote_records"], 0)

    def test_same_size_rewrite_with_restored_mtime_fails(self):
        self.cold()
        before = self.h.p1.stat()
        raw = self.h.p1.read_bytes()
        self.h.p1.write_bytes(b"X" + raw[1:])
        os.utime(self.h.p1, ns=(before.st_atime_ns, before.st_mtime_ns))
        with self.assertRaisesRegex(ContractError, "SOURCE_PREFIX_HASH_CHANGED"):
            self.read()

    def test_identity_checks_also_catch_a_missed_change_event(self):
        self.cold()
        raw = self.h.p1.read_bytes()
        with patch.object(self.cache._proofs._watch, "changed", return_value=False):
            self.h.p1.write_bytes(b"X" + raw[1:])
            with self.assertRaisesRegex(ContractError, "SOURCE_PREFIX_HASH_CHANGED"):
                self.read()

    def test_identical_atomic_replacement_reverifies_original_bytes(self):
        cold = self.cold()
        replacement = self.h.p1.with_name("replacement")
        replacement.write_bytes(self.h.p1.read_bytes())
        replacement.chmod(0o600)
        os.replace(replacement, self.h.p1)
        self.assertEqual(self.read(), cold)
        self.assertEqual(self.cache.counters()["original_proof_full_sha256_verifications"], 2)

    def test_change_during_semantic_consumption_cannot_publish_cached_result(self):
        self.cold()
        merge = self.cache._merge
        changed = [False]
        def racing_merge(*args):
            if not changed[0]:
                changed[0] = True
                raw = self.h.p1.read_bytes()
                self.h.p1.write_bytes(b"X" + raw[1:])
            return merge(*args)
        with patch.object(self.cache, "_merge", side_effect=racing_merge):
            with self.assertRaisesRegex(ContractError, "READINESS_ORIGINAL_CHANGED_DURING_PASS"):
                self.read()
        self.assertFalse(self.cache._proofs._watch.available)

    def test_lost_watch_forces_complete_sha_before_rearming(self):
        cold = self.cold()
        self.cache._proofs._watch.close()
        self.assertEqual(self.read(), cold)
        self.assertEqual(self.cache.counters()["original_proof_full_sha256_verifications"], 2)

    def test_watch_failure_falls_back_to_full_original_checks(self):
        with patch.object(self.cache._proofs._watch, "arm_many", return_value=False):
            cold = self.read()
            self.assertEqual(self.read(), cold)
        stats = self.cache.counters()
        self.assertEqual(stats["original_proof_reuses"], 0)
        self.assertEqual(stats["original_proof_full_sha256_verifications"], 2)

    def test_deadline_and_monotonic_regression_reverify(self):
        cold = self.cold()
        self.clock[0] = 159.999
        self.assertEqual(self.read(), cold)
        self.assertEqual(self.cache.counters()["original_proof_full_sha256_verifications"], 1)
        self.clock[0] = 160.0
        self.assertEqual(self.read(), cold)
        self.assertEqual(self.cache.counters()["original_proof_full_sha256_verifications"], 2)
        self.clock[0] = 90.0
        self.assertEqual(self.read(), cold)
        self.assertEqual(self.cache.counters()["original_proof_full_sha256_verifications"], 3)

    def test_permissions_and_symlinks_are_rechecked_at_use(self):
        self.cold()
        self.h.p1.chmod(0o644)
        with self.assertRaisesRegex(ContractError, "SOURCE_PREFIX_FILE_CHANGED"):
            self.read()
        self.h.p1.chmod(0o600)
        self.read()
        raw = self.h.p1.read_bytes()
        target = self.h.p1.with_name("target")
        target.write_bytes(raw)
        target.chmod(0o600)
        self.h.p1.unlink()
        self.h.p1.symlink_to(target)
        with self.assertRaisesRegex(ContractError, "SOURCE_PREFIX_IDENTITY"):
            self.read()

    def test_late_old_conflict_still_requires_complete_semantic_merge(self):
        self.cold()
        self.h.rows[self.h.p2].append({"t":1000, "known_at_ms":2900, "bid":"9", "ask":"10"})
        self.h.p2.write_bytes(self.h.p2.read_bytes() + b"late original row\n")
        with self.assertRaisesRegex(ContractError, "CROSS_SEGMENT_PRICE_CONFLICT"):
            self.read()

    def test_full_reader_still_verifies_raw_bytes_with_option_enabled(self):
        self.cold()
        with patch.object(module, "verify_prefix", wraps=module.verify_prefix) as verify:
            self.cache.read_quotes(self.h.config, 3000)
            self.assertGreater(verify.call_count, 0)

    def test_invalid_proof_interval_is_rejected(self):
        for value in (True, -1, 1, 59999, 3600001):
            with self.subTest(value=value), self.assertRaisesRegex(ContractError, "READINESS_ORIGINAL_PROOF_INTERVAL"):
                VerifiedSealedReadinessCache(original_proof_reverify_ms=value)


class ColdOriginalReadTests(unittest.TestCase):
    """Count actual reads of strict JSONL sources, not mocked decoder calls."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        period = root / "period.json"
        period.write_bytes(b'{}')
        period.chmod(0o600)
        self.feed = root / "feed"
        self.feed.mkdir(mode=0o700)
        self.config = {"period_path": str(period), "period_sha256": sha_file(period),
                       "feed_directory": str(self.feed), "completed_history_sources": []}
        self.start = 1791330000000
        self.now = self.start + 40000
        self.earliest = self.now - 5000
        self.paths = []
        for index in range(40):
            path = self.feed / f"segment-{index:08d}" / "quotes.jsonl"
            path.parent.mkdir(mode=0o700)
            self.source(path, self.start + index * 1000, ended=index < 39)
            self.paths.append(path)
        seal(self.config, 8)
        self.pins, self.pages = inventory_index(self.config, self.paths)
        self.assertEqual(len(self.pins), 32)
        self.clock = [100.0]

    def source(self, path, stamp, *, ended=True, stale=False, period_sha=None):
        header = {"source": "IB_TOOL_GATEWAY", "instrument": "EUR.USD",
                  "execution_domain": "PAPER", "period_sha256": period_sha or self.config["period_sha256"],
                  "write_operations_allowed": [], "broker_orders_sent": 0}
        quote = {"instrument": "EUR.USD", "source": "IB", "authoritative": True,
                 "stale": stale, "bid": "1.12500", "ask": "1.12502",
                 "observed_at_ms": stamp, "stale_after_ms": stamp + 5000,
                 "subscription_id": "CPU_FIXTURE", "subscription_state": "active"}
        entries = [("HEADER", header, stamp), ("QUOTE", quote, stamp + 1)]
        if ended:
            entries.append(("END", {}, stamp + 2))
        previous, lines = "0" * 64, []
        for sequence, (kind, data, at) in enumerate(entries):
            body = {"schema": shadow_source.SCHEMA, "sequence": sequence, "previous": previous,
                    "kind": kind, "data": data, "at_ms": at}
            previous = digest(body)
            lines.append(canonical({**body, "sha256": previous}))
        raw = b"\n".join(lines) + b"\n"
        path.write_bytes(raw)
        path.chmod(0o600)
        return {"path": str(path), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                "chain_head": previous, "uncommitted_tail_bytes": 0, "ended": ended}

    def cache(self, interval=60000):
        cache = VerifiedSealedReadinessCache(original_proof_reverify_ms=interval)
        self.addCleanup(cache.close)
        if cache._proofs is not None:
            cache._proofs._clock = lambda: self.clock[0]
        return cache

    def read(self, cache):
        return cache.read_quotes(self.config, self.now, earliest_ms=self.earliest)

    @contextmanager
    def reads(self):
        identities = {(path.stat().st_dev, path.stat().st_ino): str(path) for path in self.paths}
        counts = {str(path): 0 for path in self.paths}
        original = os.read
        original_preadv = getattr(os, "preadv", None)
        def count_bytes(fd, size):
            version = os.fstat(fd)
            name = identities.get((version.st_dev, version.st_ino))
            if name is not None:
                counts[name] += size
        def counted(fd, size):
            raw = original(fd, size)
            count_bytes(fd, len(raw))
            return raw
        def counted_preadv(fd, buffers, offset, flags=0):
            count = original_preadv(fd, buffers, offset, flags)
            count_bytes(fd, count)
            return count
        with patch.object(os, "read", counted), patch.object(
                os, "preadv", counted_preadv if original_preadv else None, create=True):
            yield counts

    def test_cold_miss_hashes_and_parses_each_sealed_original_in_one_read(self):
        rows, prefixes = read_quotes(self.config, self.now)
        expected = ([row for row in rows if row["t"] >= self.earliest], prefixes)
        for interval in (0, 60000):
            with self.subTest(interval=interval):
                cache = self.cache(interval)
                with self.reads() as counts:
                    self.assertEqual(self.read(cache), expected)
                for path in self.paths:
                    # The final-LF probe adds one byte to the single full pass.
                    self.assertEqual(counts[str(path)], path.stat().st_size + 1)
                stats = cache.counters()
                self.assertEqual(stats["sealed_decoding_strictly_parsed"], 32)
                self.assertEqual(stats["retired_decoded_segments"], 32)
                if interval:
                    self.assertEqual(stats["original_proof_full_sha256_verifications"], 32)
                    self.assertEqual(stats["original_proof_full_sha256_bytes"], sum(p["bytes"] for p in self.pins))

    def test_warm_and_expired_proof_keep_their_original_raw_read_paths(self):
        cache = self.cache()
        cold = self.read(cache)
        if not cache._proofs._watch.available:
            self.skipTest("continuous file change observation unavailable")
        with self.reads() as warm:
            self.assertEqual(self.read(cache), cold)
        for pin in self.pins:
            self.assertEqual(warm[pin["path"]], 0)
        self.clock[0] = 160.0
        with self.reads() as expired:
            self.assertEqual(self.read(cache), cold)
        for pin in self.pins:
            self.assertEqual(expired[pin["path"]], pin["bytes"])
        stats = cache.counters()
        self.assertEqual(stats["sealed_decoding_strictly_parsed"], 32)
        self.assertEqual(stats["original_proof_reuses"], 32)
        self.assertEqual(stats["original_proof_full_sha256_verifications"], 64)

    def test_wrong_pin_hash_wins_over_strict_parser_failure(self):
        cache = self.cache()
        path = self.paths[0]
        with path.open("r+b") as stream:
            stream.write(b"X")
        with self.assertRaisesRegex(ContractError, "SOURCE_PREFIX_HASH_CHANGED"):
            self.read(cache)
        self.assertIsNone(cache._history)
        self.assertEqual(cache._proofs._entries, {})

    def test_period_and_archive_metadata_fail_closed_in_a_cold_miss(self):
        for interval in (0, 60000):
            for wrong_period, wrong_head in ((True, False), (False, True), (True, True)):
                with self.subTest(interval=interval, period=wrong_period, head=wrong_head):
                    cache = self.cache(interval)
                    pin = self.source(self.paths[0], self.start,
                                      period_sha="1" * 64 if wrong_period else None)
                    if wrong_head:
                        pin["chain_head"] = "f" * 64
                    pins = [pin, *self.pins[1:]]
                    # The existing sealed reader rejects a mismatched pin
                    # before returning a header, including a doubly bad input.
                    error = "SOURCE_ARCHIVE_PREFIX_CHANGED" if wrong_head else "CROSS_PERIOD_FEED"
                    with patch.object(module, "inventory_index", return_value=(pins, self.pages)):
                        with self.assertRaisesRegex(ContractError, error):
                            self.read(cache)
                    self.assertIsNone(cache._history)
                    if interval:
                        self.assertEqual(cache._proofs._entries, {})

    def test_decode_eviction_preserves_proof_counters_and_deadline(self):
        cache = self.cache()
        self.earliest = self.start
        with patch.object(module, "MAX_CACHED_QUOTE_RECORDS", 0):
            cold = self.read(cache)
            if not cache._proofs._watch.available:
                self.skipTest("continuous file change observation unavailable")
            self.clock[0] = 159.999
            self.assertEqual(self.read(cache), cold)
            stats = cache.counters()
            self.assertEqual(stats["original_proof_reuses"], 32)
            self.assertEqual(stats["original_proof_full_sha256_verifications"], 32)
            self.assertEqual(cache._proofs._full_at, 100.0)
            self.clock[0] = 160.0
            self.assertEqual(self.read(cache), cold)
            self.assertEqual(cache.counters()["original_proof_full_sha256_verifications"], 64)

    def test_matching_hash_keeps_semantic_rejection_without_a_new_proof(self):
        cache = self.cache()
        pin = self.source(self.paths[0], self.start, stale=True)
        pins = [pin, *self.pins[1:]]
        with patch.object(module, "inventory_index", return_value=(pins, self.pages)):
            with self.assertRaisesRegex(ValueError, "QUOTE_AUTHORITY"):
                self.read(cache)
        self.assertIsNone(cache._history)
        self.assertEqual(cache._proofs._entries, {})

    def test_atomic_replacement_during_cold_decode_cannot_publish_a_proof(self):
        cache = self.cache()
        original = shadow_source._decode_source
        path = self.paths[0]
        raw = path.read_bytes()
        def replace_after_decode(lines, now):
            result = original(lines, now)
            replacement = path.with_name("replacement")
            replacement.write_bytes(raw)
            replacement.chmod(0o600)
            replacement.replace(path)
            return result
        with patch.object(shadow_source, "_decode_source", replace_after_decode):
            with self.assertRaisesRegex(ContractError, "SOURCE_PREFIX_FILE_CHANGED"):
                self.read(cache)
        self.assertIsNone(cache._history)
        self.assertEqual(cache._proofs._entries, {})

    def test_cold_read_still_checks_watch_and_identities_at_finish(self):
        cache = self.cache()
        original = cache._merge
        path = self.paths[0]
        raw = path.read_bytes()
        changed = False
        def replace_during_merge(*args):
            nonlocal changed
            if not changed:
                changed = True
                replacement = path.with_name("replacement")
                replacement.write_bytes(raw)
                replacement.chmod(0o600)
                replacement.replace(path)
            return original(*args)
        with patch.object(cache, "_merge", replace_during_merge):
            with self.assertRaisesRegex(ContractError, "READINESS_ORIGINAL_CHANGED_DURING_PASS"):
                self.read(cache)
        self.assertIsNone(cache._history)
        self.assertEqual(cache._proofs._entries, {})


class UncachedSealedVerificationTests(unittest.TestCase):
    """The cache hint must never alter the bytes checked or error policy."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "sealed"
        self.raw = b"original sealed bytes" * 1000 + b"\n"
        self.path.write_bytes(self.raw)
        self.path.chmod(0o600)
        self.pin = {"path": str(self.path), "bytes": len(self.raw), "ended": True,
                    "sha256": hashlib.sha256(self.raw).hexdigest()}
        self.real_read = os.read

    def preadv(self, fd, buffers, offset, flags):
        self.assertEqual(offset, -1)
        self.assertEqual(flags, getattr(os, "RWF_DONTCACHE", 0x80))
        data = self.real_read(fd, min(len(buffers[0]), 997))
        buffers[0][:len(data)] = data
        return len(data)

    def verify(self, stats):
        return source_archive.verify_prefix(
            self.pin, sealed=True, prefer_uncached=True, read_stats=stats)

    def test_default_archive_consumer_keeps_ordinary_read(self):
        with patch.object(os, "preadv", side_effect=AssertionError("unexpected hint"), create=True):
            self.assertIsNone(source_archive.verify_prefix(self.pin, sealed=True))

    def test_successful_short_reads_hash_every_original_byte(self):
        stats = {}
        with patch.object(source_archive.sys, "platform", "linux"), patch.object(
                os, "preadv", self.preadv, create=True):
            self.assertIsNone(self.verify(stats))
        self.assertEqual(stats["uncached_read_bytes"], len(self.raw))
        self.assertGreater(stats["uncached_read_calls"], 1)
        self.assertNotIn("fallback_read_bytes", stats)

    def test_unsupported_hint_falls_back_once_and_still_verifies_sha(self):
        for code in (errno.EOPNOTSUPP, errno.EINVAL, errno.ENOSYS):
            with self.subTest(code=code):
                stats = {}
                with patch.object(source_archive.sys, "platform", "linux"), patch.object(
                        os, "preadv", side_effect=OSError(code, "unsupported"), create=True) as call:
                    self.verify(stats)
                self.assertEqual(call.call_count, 1)
                self.assertEqual(stats["uncached_unsupported_files"], 1)
                self.assertEqual(stats["fallback_read_bytes"], len(self.raw))
        self.path.write_bytes(b"X" + self.raw[1:])
        with patch.object(source_archive.sys, "platform", "linux"), patch.object(
                os, "preadv", side_effect=OSError(errno.EOPNOTSUPP, "unsupported"), create=True):
            with self.assertRaisesRegex(ContractError, "SOURCE_PREFIX_HASH_CHANGED"):
                self.verify({})

    def test_fallback_after_short_read_continues_same_file_offset(self):
        calls = []
        def changed_support(fd, buffers, offset, flags):
            calls.append(None)
            if len(calls) == 2:
                raise OSError(errno.EOPNOTSUPP, "unsupported")
            return self.preadv(fd, buffers, offset, flags)
        stats = {}
        with patch.object(source_archive.sys, "platform", "linux"), patch.object(
                os, "preadv", changed_support, create=True):
            self.verify(stats)
        self.assertEqual(stats["uncached_read_bytes"], 997)
        self.assertEqual(stats["fallback_read_bytes"], len(self.raw) - 997)
        self.assertEqual(len(calls), 2)

    def test_io_error_is_not_treated_as_unsupported_hint(self):
        stats = {}
        with patch.object(source_archive.sys, "platform", "linux"), patch.object(
                os, "preadv", side_effect=OSError(errno.EIO, "read failed"), create=True):
            with self.assertRaises(OSError) as failed:
                self.verify(stats)
        self.assertEqual(failed.exception.errno, errno.EIO)
        self.assertFalse(stats)

    def test_early_eof_remains_failure(self):
        with patch.object(source_archive.sys, "platform", "linux"), patch.object(
                os, "preadv", return_value=0, create=True):
            with self.assertRaisesRegex(ContractError, "SOURCE_PREFIX_SHORT_READ"):
                self.verify({})

    def test_unavailable_api_uses_visible_ordinary_fallback(self):
        stats = {}
        with patch.object(os, "preadv", None, create=True):
            self.verify(stats)
        self.assertEqual(stats["uncached_unavailable_files"], 1)
        self.assertEqual(stats["fallback_read_bytes"], len(self.raw))

    def test_python_build_without_flags_uses_visible_ordinary_fallback(self):
        stats = {}
        with patch.object(source_archive.sys, "platform", "linux"), patch.object(
                os, "preadv", side_effect=NotImplementedError("flags unavailable"), create=True) as call:
            self.verify(stats)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(stats["uncached_unavailable_files"], 1)
        self.assertEqual(stats["fallback_read_bytes"], len(self.raw))

    def test_hint_is_not_accepted_for_an_unsealed_prefix(self):
        with self.assertRaisesRegex(ContractError, "UNCACHED_READ_REQUIRES_SEALED_PREFIX"):
            source_archive.verify_prefix(self.pin, prefer_uncached=True)


class SharedChangeWatchTests(unittest.TestCase):
    def test_many_originals_use_one_descriptor_and_catch_nonfirst_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            paths = []
            for n in range(64):
                root = Path(td)/str(n)
                root.mkdir()
                path = root/"quotes.jsonl"
                path.write_bytes(b"original\n")
                paths.append(path)
            watch = _ChangeWatch()
            self.addCleanup(watch.close)
            before = len(list(Path("/proc/self/fd").iterdir())) if Path("/proc/self/fd").is_dir() else None
            if not watch.arm_many(paths):
                self.skipTest("inotify unavailable")
            if before is not None:
                self.assertEqual(len(list(Path("/proc/self/fd").iterdir())), before+1)
            self.assertFalse(watch.changed())
            paths[-1].write_bytes(b"mutated!\n")
            self.assertTrue(watch.changed())

if __name__ == "__main__":
    unittest.main()
