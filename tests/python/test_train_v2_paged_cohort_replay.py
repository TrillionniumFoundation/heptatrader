"""Adversarial frozen cohort paging. Synthetic windows are not market data."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from historical_training.common import sha
from train_v2.contracts import ContractError, digest
from train_v2.paged_price_cohort_replay import (
    combine_frozen_windows, pinned_receipt,
    same_closeout_without_ephemeral_audit_id, require_archive,
    audit_existing_frozen_12cell,
)
from train_v2.price_cohort_closeout import verify_final_price_audit

FROZEN = "c"*64
NEW = "d"*64


def old():
    return {
        "original_sealed_segment_count": 269,
        "original_sealed_archive_fingerprint_sha256": FROZEN,
    }


def view(slot, *, active_extra=0):
    return {
        "schema": "heptatrader.train-v2-paged-original-price-window.v1",
        "sealed_segments": 286,
        "numeric_segments_total": 287,
        "source_fingerprint_sha256": NEW,
        "source_archive_page_count": 9,
        "source_archive_page_sha256": ["e"*64],
        "verified_original_bytes": 275000000+active_extra,
        "verified_original_quote_ids": 136000,
        "unsealed_newest_excluded": True,
        "frozen_original_prefix_segments": 269,
        "frozen_original_prefix_fingerprint_sha256": FROZEN,
        "all_original_numeric_segment_proofs_verified": True,
        "window_from_ms": 1000000+slot*4800000,
        "window_until_ms": 4700000+slot*4800000,
        "window_quote_count": 1,
        "rows": [(1000000+slot*4800000, 990000+slot*4800000,
                  992000+slot*4800000, str(slot)*64, "1.105")],
        "broker_orders_sent": 0,
        "trade_authority": False,
    }


class PagedCohortFenceTests(unittest.TestCase):
    def setUp(self):
        self.previous=old()
        self.windows=[view(i) for i in range(3)]

    def combine(self):
        return combine_frozen_windows(self.windows,self.previous)

    def test_three_cohort_windows_only_retain_chosen_quotes(self):
        result=self.combine()
        self.assertEqual(result["sealed_segments"],286)
        self.assertEqual(result["frozen_prefix_segment_count"],269)
        self.assertEqual(result["frozen_prefix_sha256"],FROZEN)
        self.assertEqual(result["selected_original_quote_count"],3)
        self.assertEqual(len(result["rows"]),3)

    def test_new_active_file_bytes_growth_allowed_without_reusing_it(self):
        self.windows[1]["verified_original_bytes"]+=900000
        self.windows[2]["verified_original_bytes"]+=1900000
        result=self.combine()
        self.assertEqual(result["source_fingerprint_sha256"],NEW)
        self.assertTrue(result["sealed_segments"]>result["frozen_prefix_segment_count"])

    def test_changed_frozen_prefix_rejected_even_if_current_head_consistent(self):
        self.windows[1]["frozen_original_prefix_fingerprint_sha256"]="a"*64
        with self.assertRaisesRegex(ContractError,"FROZEN_PREFIX_OR_PRICE"):
            self.combine()

    def test_naturally_rotated_sealed_archive_between_scans_rejected(self):
        self.windows[1]["sealed_segments"]+=1
        with self.assertRaisesRegex(ContractError,"SOURCE_ROTATED"):
            self.combine()

    def test_rehash_changed_sealed_source_fingerprint_rejected(self):
        self.windows[2]["source_fingerprint_sha256"]="9"*64
        with self.assertRaisesRegex(ContractError,"SOURCE_ROTATED"):
            self.combine()

    def test_sealed_archive_page_change_rejected(self):
        self.windows[1]["source_archive_page_sha256"]=["5"*64]
        with self.assertRaisesRegex(ContractError,"SOURCE_ROTATED"):
            self.combine()

    def test_sealed_original_quote_cardinality_change_rejected(self):
        self.windows[2]["verified_original_quote_ids"]+=1
        with self.assertRaisesRegex(ContractError,"SOURCE_ROTATED"):
            self.combine()

    def test_latest_unsealed_quote_cannot_be_admitted_as_sealed(self):
        self.windows[2]["unsealed_newest_excluded"]=False
        with self.assertRaisesRegex(ContractError,"SOURCE_ROTATED"):
            self.combine()
        self.windows=[view(i) for i in range(3)]
        self.windows[2]["all_original_numeric_segment_proofs_verified"]=False
        with self.assertRaisesRegex(ContractError,"FROZEN_PREFIX_OR_PRICE"):
            self.combine()

    def test_duplicate_source_receipts_between_windows_rejected(self):
        self.windows[1]["rows"][0]=(
            self.windows[1]["rows"][0][0],
            self.windows[1]["rows"][0][1],
            self.windows[1]["rows"][0][2],
            self.windows[0]["rows"][0][3],
            self.windows[1]["rows"][0][4])
        with self.assertRaisesRegex(ContractError,"DUPLICATE_QUOTE"):
            self.combine()

    def test_overlapping_windows_not_counted_independent(self):
        self.windows[1]["window_from_ms"]=self.windows[0]["window_until_ms"]
        with self.assertRaisesRegex(ContractError,"REUSED_OR_OVERLAPPED"):
            self.combine()

    def test_missing_third_window_denied_without_reducing_denominator(self):
        self.windows.pop()
        with self.assertRaisesRegex(ContractError,"THREE_SOURCE_WINDOWS"):
            self.combine()

    def test_source_page_scope_or_authority_cannot_be_forged(self):
        self.windows[1]["trade_authority"]=True
        with self.assertRaisesRegex(ContractError,"FROZEN_PREFIX_OR_PRICE"):
            self.combine()
        self.windows=[view(i) for i in range(3)]
        self.windows[1]["broker_orders_sent"]=1
        with self.assertRaisesRegex(ContractError,"FROZEN_PREFIX_OR_PRICE"):
            self.combine()

    def test_not_all_quoterows_attested_rejects(self):
        self.windows[1]["window_quote_count"]=2
        with self.assertRaisesRegex(ContractError,"ROW_COUNT"):
            self.combine()

    def test_closeout_may_only_ignore_separate_audit_digest(self):
        first={"terminal_state":"ALL_ORIGINAL_PRICES_OBSERVED",
               "largest_abs_hindsight_midprice_move_bps":"3.3",
               "verified_price_audit_id":"a"*64,"closeout_id":"b"*64}
        later={**first,"verified_price_audit_id":"c"*64,"closeout_id":"d"*64}
        self.assertTrue(same_closeout_without_ephemeral_audit_id(first,later))
        later["largest_abs_hindsight_midprice_move_bps"]="3.4"
        self.assertFalse(same_closeout_without_ephemeral_audit_id(first,later))

    def test_pinned_immutable_prior_receipt_change_denied(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"old.json"
            original={"schema":"heptatrader.train-v2-price-cohort-closeout.v1",
                      "terminal_state":"HOLD"}
            original["closeout_id"]=digest(original)
            p.write_text(json.dumps(original,sort_keys=True)+"\n")
            frozen_sha=sha(p)
            self.assertEqual(pinned_receipt(p,frozen_sha,original["schema"]),original)
            p.write_text(p.read_text()+" ")
            with self.assertRaisesRegex(ContractError,"PRIOR_RECEIPT_CHANGED"):
                pinned_receipt(p,frozen_sha,original["schema"])

    def test_wrong_original_source_loader_never_autocorrects(self):
        with self.assertRaisesRegex(ContractError,"LOADER_WRONG_SOURCE"):
            require_archive({"rows":[]},{"source_root":"/one",
                             "period_sha256":"b"*64},"/two","b"*64)
        with self.assertRaisesRegex(ContractError,"LOADER_WRONG_SOURCE"):
            require_archive({"rows":[]},{"source_root":"/one",
                             "period_sha256":"b"*64},"/one","a"*64)
