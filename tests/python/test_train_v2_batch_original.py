"""Batched original source regression. Historical labels are not trade alpha."""
from __future__ import annotations

import copy
import unittest
from pathlib import Path
from unittest.mock import patch

from train_v2.contracts import ContractError
from train_v2.paged_batch_original import verify_three_windows_once
from train_v2.paged_price_window import verify_paged_price_window

PERIOD = "e"*64
STEP = 1800000


class BatchOriginalTests(unittest.TestCase):
    def setUp(self):
        # Reuse existing canonical synthetic sealed-source fixture without
        # inheriting its TestCase into this new discovered module.
        from test_train_v2_paged_price_window import VerifiedPagedPriceTests
        self.fixture = VerifiedPagedPriceTests(
            methodName="test_legacy_fingerprint_is_exact_for_small_source")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.start = self.fixture.base

    def bounds(self, *indices):
        return [(self.start+i*STEP, self.start+i*STEP+5000)
                for i in indices]

    def batch(self, indices=(0, 2, 4), frozen=None):
        return verify_three_windows_once(
            self.root, PERIOD, self.bounds(*indices),
            frozen_prefix_segments=frozen)

    def test_exact_same_three_original_one_window_results(self):
        self.fixture.build(7)
        self.fixture.archived()
        batched = self.batch(frozen=7)
        self.assertEqual(batched["full_original_scan_count"], 1)
        self.assertEqual(batched["verified_sealed_segments"], 7)
        self.assertEqual(batched["selected_quotes"], 3)
        for i, view in zip((0,2,4), batched["views"]):
            old = verify_paged_price_window(
                self.root, PERIOD, *self.bounds(i)[0],
                frozen_prefix_segments=7)
            self.assertEqual(view, old)
        self.assertFalse(batched["trade_authority"])
        self.assertEqual(batched["broker_orders_sent"], 0)

    def test_three_disjoint_windows_use_exact_one_first_pass_per_segment(self):
        self.fixture.build(7)
        from train_v2.paged_batch_original import read_source
        calls = []
        def recorded(file):
            calls.append(Path(file))
            return read_source(file)
        with patch("train_v2.paged_batch_original.read_source", side_effect=recorded):
            result = self.batch()
        self.assertEqual(len(calls), 7)
        self.assertEqual(len(set(calls)), 7)
        self.assertEqual(result["full_original_scan_count"], 1)
        self.assertEqual(sum(v["window_quote_count"] for v in result["views"]),3)

    def test_321_segments_page_complete_without_legacy_cap_change(self):
        self.fixture.build(321)
        archive = self.fixture.archived()
        self.assertEqual(archive["total_pages"], 10)
        batch = self.batch((305,310,320),frozen=269)
        self.assertEqual(batch["verified_sealed_segments"],321)
        self.assertEqual(batch["selected_quotes"],3)
        self.assertEqual(batch["views"][0]["source_archive_page_count"],10)
        self.assertEqual(batch["views"][2]["window_quote_count"],1)
        self.assertEqual(len({w["source_fingerprint_sha256"]
                              for w in batch["views"]}),1)

    def test_append_only_sealed_successor_changes_head_not_frozen_prefix(self):
        self.fixture.build(7)
        original = self.batch(frozen=7)
        self.fixture.make_one(7)
        grown = self.batch(frozen=7)
        self.assertNotEqual(original["sealed_source_fingerprint_sha256"],
                            grown["sealed_source_fingerprint_sha256"])
        self.assertEqual(original["frozen_prefix_sha256"],
                         grown["frozen_prefix_sha256"])
        self.assertEqual(grown["views"][0][
                         "sealed_successors_after_frozen_prefix"],1)

    def test_active_unsealed_newest_excluded_without_waiting_for_it(self):
        self.fixture.build(7,open_last=True)
        result = self.batch(indices=(0,2,4))
        self.assertTrue(result["views"][0]["unsealed_newest_excluded"])
        self.assertEqual(result["verified_sealed_segments"],6)
        self.assertEqual(result["selected_quotes"],3)

    def test_missing_old_original_is_never_hidden_by_paging(self):
        files = self.fixture.build(65)
        self.fixture.archived()
        files[1].unlink()
        # Original source decoder may reject using the pre-existing local
        # integrity exception rather than the V2 feature exception.
        with self.assertRaises(ValueError):
            self.batch(indices=(10,30,60))

    def test_corrupt_early_archive_page_fails_all_windows(self):
        self.fixture.build(65)
        self.fixture.archived()
        from laya_cell.source_archive import directory
        p = directory(self.fixture.config)/"page-00000000.json"
        p.write_bytes(p.read_bytes().replace(b'"index":0',b'"index":99'))
        # Original source decoder may reject using the pre-existing local
        # integrity exception rather than the V2 feature exception.
        with self.assertRaises(ValueError):
            self.batch(indices=(10,30,60))

    def test_duplicated_source_start_denied(self):
        self.fixture.build(7)
        self.fixture.make_one(0,suffix=777)
        with self.assertRaisesRegex(ValueError,"DUPLICATE_SEGMENT_START"):
            self.batch()

    def test_missing_third_window_is_not_a_zero_price_label(self):
        self.fixture.build(7)
        with self.assertRaisesRegex(ContractError,"ALL_THREE_SOURCE_WINDOWS_REQUIRED"):
            self.batch(indices=(0,2,8))

    def test_extra_and_overlapping_windows_rejected(self):
        self.fixture.build(7)
        for bad in ((0,2),(0,2,4,6)):
            with self.assertRaisesRegex(ContractError,"EXACT_THREE"):
                self.batch(indices=bad)
        for windows in (self.bounds(0,0,4),
                        [self.bounds(0)[0],self.bounds(2)[0],
                         self.bounds(2)[0]]):
            with self.assertRaisesRegex(ContractError,"ORDER_OR_OVERLAP"):
                verify_three_windows_once(self.root,PERIOD,windows)

    def test_overlarge_span_or_changed_scope_denied(self):
        self.fixture.build(7)
        too_long = self.bounds(0,2,4)
        too_long[0] = (too_long[0][0],too_long[0][0]+7200001)
        with self.assertRaisesRegex(ContractError,"WINDOW_TIME_OR_DURATION"):
            verify_three_windows_once(self.root,PERIOD,too_long)
        with self.assertRaisesRegex(ContractError,"PERIOD_INVALID"):
            verify_three_windows_once(self.root,"A"*64,self.bounds(0,2,4))

    def test_frozen_prefix_loss_rejected(self):
        self.fixture.build(7)
        with self.assertRaisesRegex(ContractError,"FROZEN_PREFIX_MISSING"):
            self.batch(frozen=8)

    def test_second_inventory_page_change_denied(self):
        self.fixture.build(65)
        self.fixture.archived()
        from train_v2.paged_batch_original import inventory
        count=[0]
        def changed(cfg,paths):
            pins,pages=inventory(cfg,paths)
            count[0]+=1
            if count[0]==2:
                pages=[*pages[:-1],{**pages[-1],"sha256":"1"*64}]
            return pins,pages
        with patch("train_v2.paged_batch_original.inventory",side_effect=changed):
            with self.assertRaisesRegex(ContractError,"PAGE_FRONTIER_CHANGED"):
                self.batch(indices=(10,30,60))

    def test_original_byte_mutation_between_double_reads_rejected(self):
        self.fixture.build(7)
        from train_v2.paged_batch_original import sealed_original_broker_expiry
        def changed(file,spec):
            original,seals=sealed_original_broker_expiry(file,spec)
            seals["verified_prefix_sha256"]="2"*64
            return original,seals
        with patch("train_v2.paged_batch_original.sealed_original_broker_expiry",
                   side_effect=changed):
            with self.assertRaisesRegex(ContractError,"DOUBLE_READ_SOURCE_CHANGED"):
                self.batch()

    def test_selected_quote_budget_is_not_silently_truncated(self):
        self.fixture.build(7)
        with patch("train_v2.paged_batch_original.MAX_WINDOW_QUOTES",0):
            with self.assertRaisesRegex(ContractError,"WINDOW_ROW_BUDGET"):
                self.batch()
