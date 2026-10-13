"""Adversarial 320+ segment paging: full original inventory, bounded window."""
from __future__ import annotations

from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from laya_cell.shadow_source import canonical as canonical_source
from laya_cell.shadow_source import digest as digest_source
from laya_cell.source_archive import seal, directory
from laya_cell.integrity import ContractError
from train_v2.paged_price_window import verify_paged_price_window
from train_v2.postmodel_requote import load_original_quote_archive

PERIOD = "e" * 64
THIRTY_MINUTES = 1800000


class VerifiedPagedPriceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "feed"
        self.root.mkdir(mode=0o700)
        self.base = ((int(time.time()*1000) - 340 * THIRTY_MINUTES) // 1000)*1000
        self.config = {
            "feed_directory": str(self.root),
            "period_sha256": PERIOD,
        }

    def make_one(self, i, *, ended=True, suffix=None, price=None):
        at = self.base + i * THIRTY_MINUTES
        directory_name = f"segment-{at*1000000:019d}-{(i if suffix is None else suffix):08x}"
        folder = self.root / directory_name
        folder.mkdir(mode=0o700)
        file = folder / "quotes.jsonl"
        bid = str(Decimal("1.1") + Decimal(i)/Decimal(10000000)) if price is None else price
        ask = str(Decimal(bid) + Decimal("0.00003"))
        header = {
            "source": "IB_TOOL_GATEWAY", "instrument": "EUR.USD",
            "execution_domain": "PAPER", "period_sha256": PERIOD,
            "write_operations_allowed": [], "broker_orders_sent": 0,
        }
        quote = {
            "instrument": "EUR.USD", "source": "IB",
            "authoritative": True, "stale": False,
            "bid": bid, "ask": ask, "observed_at_ms": at + 1000,
            "stale_after_ms": at + 5000,
            "subscription_id": "source-contract-test",
            "subscription_state": "active",
        }
        records = [("HEADER", header, at), ("QUOTE", quote, at+1100)]
        if ended:
            records.append(("END", {"broker_orders_sent": 0}, at+1200))
        previous = "0"*64
        lines = []
        for n, (kind, data, known) in enumerate(records):
            base = {
                "schema": "heptatrader.laya-shadow-quotes.v1",
                "sequence": n, "previous": previous,
                "at_ms": known, "kind": kind, "data": data,
            }
            row = {**base, "sha256": digest_source(base)}
            previous = row["sha256"]
            lines.append(canonical_source(row))
        file.write_bytes(b"\n".join(lines)+b"\n")
        file.chmod(0o600)
        return file

    def build(self, count, *, open_last=False):
        paths = []
        for i in range(count):
            paths.append(self.make_one(i, ended=not(open_last and i==count-1)))
        return paths

    def archived(self):
        # The writer creates <=8 original 32-source pages per bounded run.
        while True:
            result = seal(self.config, 8)
            if result["pages_added"] == 0:
                break
        return result

    def window(self, first=0, last=1):
        return verify_paged_price_window(
            self.root, PERIOD, self.base + first*THIRTY_MINUTES,
            self.base + last*THIRTY_MINUTES + 5000)

    def test_321_full_segments_pages_without_raising_legacy_cap(self):
        self.build(321)
        pages = self.archived()
        self.assertEqual(pages["total_pages"], 10)
        with self.assertRaisesRegex(ValueError, "REQUOTING_NUMERIC_SEGMENT_BUDGET"):
            load_original_quote_archive(self.root, PERIOD)
        seen = self.window(319, 320)
        self.assertEqual(seen["numeric_segments_total"], 321)
        self.assertEqual(seen["sealed_segments"], 321)
        self.assertEqual(seen["source_archive_page_count"], 10)
        self.assertEqual(seen["verified_original_quote_ids"], 321)
        self.assertEqual(seen["window_quote_count"], 2)
        self.assertEqual(len(seen["rows"]), 2)
        self.assertTrue(seen["all_original_numeric_segment_proofs_verified"])
        self.assertFalse(seen["trade_authority"])
        self.assertEqual(seen["broker_orders_sent"], 0)

    def test_new_appended_segment_preserves_frozen_full_prefix_proof(self):
        self.build(3)
        frozen = self.window(0, 2)
        self.make_one(3)
        grown = verify_paged_price_window(
            self.root, PERIOD, self.base, self.base+2*THIRTY_MINUTES+5000,
            frozen_prefix_segments=3)
        self.assertEqual(grown["frozen_original_prefix_fingerprint_sha256"],
                         frozen["source_fingerprint_sha256"])
        self.assertNotEqual(grown["source_fingerprint_sha256"],
                            frozen["source_fingerprint_sha256"])
        self.assertEqual(grown["sealed_successors_after_frozen_prefix"], 1)
        self.assertEqual(grown["window_quote_count"],3)

    def test_original_prefix_missing_refuses_posthoc_substitution(self):
        self.build(2)
        with self.assertRaisesRegex(ContractError,"PAGED_SOURCE_FROZEN_PREFIX_MISSING"):
            verify_paged_price_window(
                self.root,PERIOD,self.base,self.base+THIRTY_MINUTES+5000,
                frozen_prefix_segments=3)

    def test_zero_or_boolean_frozen_prefix_refused(self):
        self.build(2)
        for value in (0,-1,True,"2"):
            with self.subTest(value=value),self.assertRaisesRegex(
                    ContractError,"PAGED_SOURCE_FROZEN_PREFIX_COUNT_INVALID"):
                verify_paged_price_window(
                    self.root,PERIOD,self.base,self.base+THIRTY_MINUTES+5000,
                    frozen_prefix_segments=value)

    def test_legacy_fingerprint_is_exact_for_small_source(self):
        self.build(3)
        first = load_original_quote_archive(self.root, PERIOD)
        second = self.window(0, 2)
        self.assertEqual(first["source_fingerprint_sha256"],
                         second["source_fingerprint_sha256"])
        self.assertEqual(first["rows"], second["rows"])
        self.assertEqual(first["sealed_segments"], second["sealed_segments"])

    def test_middle_original_segment_deleted_never_silently_omitted(self):
        paths = self.build(64)
        self.archived()
        paths[18].unlink()
        with self.assertRaises(ContractError):
            self.window(63, 63)

    def test_middle_archive_page_corruption_refused_even_if_window_late(self):
        self.build(97)
        self.archived()
        page = directory(self.config) / "page-00000000.json"
        page.write_bytes(page.read_bytes().replace(b'"index":0', b'"index":7'))
        with self.assertRaisesRegex(ContractError, "SOURCE_ARCHIVE_PAGE"):
            self.window(96, 96)

    def test_independent_checkpoint_ledger_truncation_refused(self):
        self.build(65)
        self.archived()
        ledger = directory(self.config) / "ledger.jsonl"
        ledger.write_bytes(ledger.read_bytes()[:-1])
        with self.assertRaisesRegex(ContractError, "SOURCE_ARCHIVE_LEDGER"):
            self.window(64, 64)

    def test_old_archived_source_byte_changed_detected(self):
        paths = self.build(65)
        self.archived()
        paths[0].write_bytes(paths[0].read_bytes().replace(b'1.10003', b'1.10004'))
        with self.assertRaises(ContractError):
            self.window(64, 64)

    def test_duplicate_source_start_refused(self):
        self.build(2)
        self.make_one(0, suffix=12345)
        with self.assertRaisesRegex(ContractError, "PAGED_SOURCE_DUPLICATE_SEGMENT_START"):
            self.window(0, 1)

    def test_malformed_new_segment_never_ignored(self):
        self.build(2)
        bad = self.root / "segment-bad"
        bad.mkdir(mode=0o700)
        (bad / "quotes.jsonl").write_bytes(b"broken")
        with self.assertRaisesRegex(ContractError, "PAGED_SOURCE_MALFORMED_SEGMENT"):
            self.window(0, 1)

    def test_unsealed_old_segment_fails_not_reclassified_as_censored(self):
        self.make_one(0, ended=False)
        self.make_one(1, ended=True)
        with self.assertRaisesRegex(ContractError, "PAGED_SOURCE_EARLIER_UNSEALED_SEGMENT"):
            self.window(0, 1)

    def test_only_latest_unsealed_numeric_source_excluded(self):
        self.build(4, open_last=True)
        seen = self.window(1, 2)
        self.assertEqual(seen["sealed_segments"], 3)
        self.assertEqual(seen["numeric_segments_total"], 4)
        self.assertTrue(seen["unsealed_newest_excluded"])
        self.assertEqual(len(seen["rows"]), 2)

    def test_window_limit_must_not_be_raised_implicitly(self):
        self.build(4)
        with self.assertRaisesRegex(ContractError, "PAGED_WINDOW_TIME_OR_SCOPE"):
            verify_paged_price_window(self.root, PERIOD,
                self.base, self.base+5*THIRTY_MINUTES)
        for bad in (None, True, "123", -1):
            with self.subTest(bad=bad), self.assertRaises(ContractError):
                verify_paged_price_window(self.root, PERIOD,bad,self.base+5000)

    def test_source_page_count_bound_rejects_not_drop(self):
        self.build(65)
        self.archived()
        with patch("train_v2.paged_price_window.MAX_PAGES_PER_SCAN", 1):
            with self.assertRaisesRegex(ContractError, "PAGED_SOURCE_CHECKPOINT_PAGE_BOUND"):
                self.window(64, 64)

    def test_window_row_budget_rejects_not_truncates(self):
        self.build(2)
        with patch("train_v2.paged_price_window.MAX_WINDOW_QUOTES", 1):
            with self.assertRaisesRegex(ContractError, "PAGED_SOURCE_WINDOW_ROW_BUDGET"):
                self.window(0, 1)

    def test_total_receipt_budget_refuses_unbounded_global_index(self):
        self.build(3)
        with patch("train_v2.paged_price_window.MAX_TOTAL_QUOTE_IDENTITIES", 2):
            with self.assertRaisesRegex(ContractError, "PAGED_SOURCE_QUOTE_ID_BUDGET"):
                self.window(1, 2)

    def test_second_inventory_new_checkpoint_fails_closed(self):
        self.build(65)
        self.archived()
        from train_v2.paged_price_window import inventory
        calls = [0]
        def alternate(config, paths):
            result = inventory(config, paths)
            calls[0] += 1
            if calls[0] == 2:
                result[1][-1] = {**result[1][-1], "sha256":"f"*64}
            return result
        with patch("train_v2.paged_price_window.inventory", side_effect=alternate):
            with self.assertRaisesRegex(ContractError, "PAGED_SOURCE_PAGE_FRONTIER_CHANGED"):
                self.window(64,64)

    def test_source_modified_after_prefix_scan_refused(self):
        files=self.build(4)
        from train_v2.paged_price_window import sealed_original_broker_expiry
        def change(p, spec):
            actual=sealed_original_broker_expiry(p,spec)
            files[0].write_bytes(files[0].read_bytes().replace(b'1.10003',b'1.10004'))
            return actual
        with patch("train_v2.paged_price_window.sealed_original_broker_expiry", side_effect=change):
            with self.assertRaises(ContractError):
                self.window(1,2)

    def test_recovery_live_alias_not_selected_as_numeric_price(self):
        self.build(2)
        folder=self.root/"segment-recovery-live"
        folder.mkdir(mode=0o700)
        (folder/"quotes.jsonl").write_bytes(b'untrusted-recovery-prefix')
        result=self.window(0,1)
        self.assertEqual(result["numeric_segments_total"],2)
        self.assertEqual(len(result["rows"]),2)
