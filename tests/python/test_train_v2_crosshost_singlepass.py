"""Independent source singlepass exact equivalence and adversarial tests.

Synthetic fixture data are never claims of real broker-signed authority.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from paged_crosshost_custody import (
    copied_original_inventory, CustodyError, canonical,
)
from paged_crosshost_singlepass import copied_original_multiwindow
import test_train_v2_independent_copy_custody as fixture_module
BASE = fixture_module.BASE
ORIGINAL = fixture_module.ORIGINAL
PERIOD = fixture_module.PERIOD


class BatchCustodyTests(unittest.TestCase):
    @staticmethod
    def windows():
        return [
            (BASE + 2*10000, BASE + 12*10000+20),
            (BASE + 24*10000, BASE + 34*10000+20),
            (BASE + 46*10000, BASE + 61*10000+20),
        ]

    def setUp(self):
        # Reuse the old synthetic source builder without inheriting (and
        # accidentally running) its independent unittest methods.
        fixture = fixture_module.SnapshotFixture(methodName="runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.root = fixture.root
        self.archive = fixture.archive
        self.segments = fixture.segments
        fixture.build(64)

    def batched(self, intervals=None):
        return copied_original_multiwindow(
            self.root, ORIGINAL, PERIOD,
            self.windows() if intervals is None else intervals)

    def test_full_source_verified_once_all_windows_match_triple_original(self):
        new = self.batched()
        self.assertEqual(new["complete_original_source_scans"], 1)
        self.assertEqual(new["numeric_segments"], 64)
        self.assertEqual(new["archived_segments"], 64)
        self.assertEqual(new["archive_pages"], 2)
        self.assertTrue(new["zero_authorization_effect"])
        for i,(begin,end) in enumerate(self.windows()):
            old = copied_original_inventory(
                self.root, ORIGINAL, PERIOD, begin, end)
            self.assertEqual(new["windows"][i]["window_rows"], old["window_rows"])
            self.assertEqual(new["windows"][i]["original_selected_quote_receipts"],
                             old["original_selected_quote_receipts"])
            for field in (
                "numeric_segments","archived_segments","archive_pages",
                "source_fingerprint_sha256","original_source_bytes",
                "original_total_unique_quote_receipts",
                "original_archive_checkpoint_chain_head"):
                self.assertEqual(new[field], old[field], field)
            self.assertEqual(new["frozen_prefix_sha256"](32),
                             old["frozen_prefix_sha256"](32))

    def test_overlapping_or_unsorted_windows_refused(self):
        w = self.windows()
        for invalid in ([w[0],w[0],w[2]],
                        list(reversed(w)),
                        [w[0],(w[0][1],w[1][1]),w[2]]):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                    CustodyError,"BATCH_WINDOWS_OVERLAP_OR_UNSORTED"):
                self.batched(invalid)

    def test_wrong_number_of_frozen_decisions_refused(self):
        with self.assertRaisesRegex(CustodyError,"BATCH_EXACT_THREE_WINDOWS"):
            self.batched(self.windows()[:2])

    def test_large_or_invalid_window_refused(self):
        w = self.windows()
        w[1] = (w[1][0],w[1][0]+7_200_001)
        with self.assertRaisesRegex(CustodyError,"BATCH_WINDOW_BOUND"):
            self.batched(w)

    def test_outside_any_price_window_corruption_is_detected(self):
        self.segments[40].write_bytes(
            self.segments[40].read_bytes().replace(b'"authoritative":true',
                                                   b'"authoritative":false',1))
        with self.assertRaises(CustodyError):
            self.batched()

    def test_outside_all_windows_archived_page_tamper_refused(self):
        p=self.archive/"page-00000001.json"
        p.write_bytes(p.read_bytes().replace(b'"index":1',b'"index":2',1))
        with self.assertRaises(CustodyError):
            self.batched()

    def test_original_archive_ledger_hash_drift_refused(self):
        p=self.archive/"ledger.jsonl"
        p.write_bytes(p.read_bytes().replace(b'"index":0',b'"index":9',1))
        with self.assertRaises(CustodyError):
            self.batched()

    def test_duplicate_original_source_ids_across_segments_refused(self):
        # byte-exact reuse of entire segment makes source ids duplicate,
        # even if the source segments are outside every selected window.
        original=self.segments[43].read_bytes()
        self.segments[44].write_bytes(original)
        with self.assertRaises(CustodyError):
            self.batched()

    def test_source_price_conflict_refused(self):
        self.segments[32].write_bytes(
            self.segments[32].read_bytes().replace(b'"ask":"1.100',
                b'"ask":"1.200',1))
        with self.assertRaises(CustodyError):
            self.batched()

    def test_invalid_scoped_root_and_privacy_refused(self):
        with self.assertRaisesRegex(CustodyError,"BATCH_ORIGINAL_ABSOLUTE_ROOT"):
            copied_original_multiwindow(self.root,"relative/path",PERIOD,
                                        self.windows())
        self.root.chmod(0o755)
        with self.assertRaisesRegex(CustodyError,"BATCH_COPY_SOURCE_ROOT_UNSAFE"):
            self.batched()
