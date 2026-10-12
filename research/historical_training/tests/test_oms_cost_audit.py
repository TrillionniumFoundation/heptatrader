"""Fail-closed, read-only regression tests for exact-snapshot OMS fee attribution."""
from __future__ import annotations
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import os
import stat
import tempfile
from types import SimpleNamespace
import unittest

from historical_training.oms_cost_audit import (
    CostEvidenceError, read_journal_prefix, read_private,
    summarize_validated_ledger, write_exclusive, percentile, strict
)


def fixtures():
    fills = [
        {"execution_id": "a", "command_id": "cmd1", "side": "BUY",
         "quantity": "10", "price": "100", "commission": "1",
         "commission_currency": "USD"},
        {"execution_id": "b", "command_id": "cmd2", "side": "SELL",
         "quantity": "10", "price": "103", "commission": "1.03",
         "commission_currency": "USD"}
    ]
    ledger = {
        "book": SimpleNamespace(paid_fees=Decimal("2.03"),
                                realized=Decimal("30"), quantity=Decimal("0")),
        "fees_complete": True, "executions": fills,
        "execution_count": 2, "command_count": 2
    }
    component = {
        "instrument": "TEST", "fees_complete": True, "ledger_complete": True,
        "execution_count": 2, "execution_details_total": 2,
        "execution_details_truncated": True, "executions": [fills[-1]],
        "command_count": 2, "fee_missing_execution_ids_total": 0,
        "fee_conflicting_execution_ids_total": 0,
        "fees_paid": "2.03", "realized_gross_pnl": "30", "position": "0",
        "gross_total_pnl": "30", "net_total_pnl": "27.97"
    }
    return ledger, component


class CostLedgerTests(unittest.TestCase):
    def test_full_ledger_not_truncated_display(self):
        ledger, component = fixtures()
        result = summarize_validated_ledger(ledger, component)
        self.assertEqual(result["unique_executions"], 2)
        self.assertEqual(result["commission_usd"], "2.03")
        self.assertEqual(result["cumulative_executed_notional_usd"], "2030")
        self.assertEqual(result["net_total_pnl_usd"], "27.97")
        self.assertTrue(result["truncated_pnl_display_tail_not_used"])

    def test_missing_fee_is_refused(self):
        ledger, component = fixtures()
        ledger["fees_complete"] = False
        with self.assertRaisesRegex(CostEvidenceError, "FEE_OR_LEDGER_NOT_COMPLETE"):
            summarize_validated_ledger(ledger, component)

    def test_duplicate_execution_refused(self):
        ledger, component = fixtures()
        ledger["executions"][1]["execution_id"] = "a"
        with self.assertRaisesRegex(CostEvidenceError, "DUPLICATE_VALIDATED_EXECUTION_ID"):
            summarize_validated_ledger(ledger, component)

    def test_mismatched_full_count_refused(self):
        ledger, component = fixtures()
        component["execution_details_total"] = 128
        with self.assertRaisesRegex(CostEvidenceError, "EXECUTION_COUNT_MISMATCH"):
            summarize_validated_ledger(ledger, component)

    def test_fee_sum_not_exact_refused(self):
        ledger, component = fixtures()
        component["fees_paid"] = "2.04"
        with self.assertRaisesRegex(CostEvidenceError, "COMMISSION_BOOK_TOTAL_MISMATCH"):
            summarize_validated_ledger(ledger, component)

    def test_missing_usd_fee_refused(self):
        ledger, component = fixtures()
        ledger["executions"][0]["commission_currency"] = "EUR"
        with self.assertRaisesRegex(CostEvidenceError, "EXECUTION_ECONOMICS_INVALID"):
            summarize_validated_ledger(ledger, component)

    def test_gross_minus_fee_mismatch_refused(self):
        ledger, component = fixtures()
        component["net_total_pnl"] = "30"
        with self.assertRaisesRegex(CostEvidenceError, "NET_PNL_NOT_GROSS_MINUS_FEES"):
            summarize_validated_ledger(ledger, component)

    def test_exact_decimal_percentiles(self):
        self.assertEqual(percentile([Decimal("1"), Decimal("2"), Decimal("3")], 50), "2")
        self.assertEqual(percentile([Decimal("1"), Decimal("2")], 50), "1.5")

    def test_duplicate_json_key_rejected(self):
        with self.assertRaisesRegex(CostEvidenceError, "DUPLICATE_JSON_KEY"):
            strict(b'{"x": 1, "x": 2}')

    def test_nonfinite_json_rejected(self):
        with self.assertRaisesRegex(CostEvidenceError, "NONFINITE_JSON_NUMBER"):
            strict(b'{"x": NaN}')


class PrefixTests(unittest.TestCase):
    def test_exact_original_prefix_survives_append_only(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "oms.jsonl"
            raw = b'{"event":"first"}\n'
            path.write_bytes(raw)
            path.chmod(0o600)
            st = path.stat()
            fp = [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns]
            with path.open("ab") as f:
                f.write(b'{"event":"next"}\n')
            self.assertEqual(read_journal_prefix(path, fp, hashlib.sha256(raw).hexdigest()), raw)

    def test_rewritten_prefix_rejected_even_if_size_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "oms.jsonl"
            raw = b'{"event":"first"}\n'
            path.write_bytes(raw)
            path.chmod(0o600)
            st = path.stat()
            fp = [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns]
            path.write_bytes(b'{"event":"other"}\n')
            with self.assertRaisesRegex(CostEvidenceError, "JOURNAL_PREFIX_SHA256_MISMATCH"):
                read_journal_prefix(path, fp, hashlib.sha256(raw).hexdigest())

    def test_truncation_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "oms.jsonl"
            raw = b'{"event":"first"}\n'
            path.write_bytes(raw)
            path.chmod(0o600)
            st = path.stat()
            fp = [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns]
            path.write_bytes(b'')
            with self.assertRaisesRegex(CostEvidenceError, "JOURNAL_REPLACED_OR_TRUNCATED"):
                read_journal_prefix(path, fp, hashlib.sha256(raw).hexdigest())

    def test_replaced_inode_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "oms.jsonl"
            raw = b'{"event":"first"}\n'
            path.write_bytes(raw)
            path.chmod(0o600)
            st = path.stat()
            fp = [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns]
            replacement = Path(d) / "replacement"
            replacement.write_bytes(raw)
            replacement.chmod(0o600)
            os.replace(replacement, path)
            with self.assertRaisesRegex(CostEvidenceError, "JOURNAL_REPLACED_OR_TRUNCATED"):
                read_journal_prefix(path, fp, hashlib.sha256(raw).hexdigest())

    def test_unsafe_public_file_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "private.json"
            path.write_bytes(b"{}")
            path.chmod(0o644)
            with self.assertRaisesRegex(CostEvidenceError, "PRIVATE_SOURCE_INVALID"):
                read_private(path, 1024)

    def test_write_once_never_overwrites_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "evidence"
            root.mkdir(mode=0o700)
            output = root / "proof.json"
            write_exclusive(output, {"state": "accepted"})
            with self.assertRaises(FileExistsError):
                write_exclusive(output, {"state": "changed"})
            self.assertEqual(json.loads(output.read_text())["state"], "accepted")
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
