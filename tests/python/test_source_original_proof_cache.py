"""Changed originals and lost observation invalidate local sealed SHA reuse."""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from laya_cell.integrity import ContractError
from laya_cell.verified_sealed_readiness_cache import VerifiedSealedReadinessCache
from laya_cell import verified_sealed_readiness_cache as module
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
