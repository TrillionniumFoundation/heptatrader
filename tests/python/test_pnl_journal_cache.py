"""Byte-bound incremental decoding for read-only PAPER PnL watch mode."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import hepta_pnl_report as pnl


def row(value):
    return (json.dumps({"amount": value}, separators=(",", ":")) + "\n").encode()


class IncrementalJournalTests(unittest.TestCase):
    def test_unchanged_bytes_reuse_verified_events_without_json_redecode(self):
        c = pnl.IncrementalOMSParser()
        raw = row("1.20") + row("2.50")
        original = c.parse(raw)
        self.assertEqual([r["amount"] for r in original], ["1.20", "2.50"])
        with patch.object(pnl, "strict_json",
                          side_effect=AssertionError("unexpected JSON replay")):
            same = c.parse(raw)
        self.assertIs(same, original)
        self.assertEqual((c.rebuilds, c.appends, c.hits), (1, 0, 1))

    def test_append_only_prefix_rechecks_original_sha_and_parses_only_tail(self):
        c = pnl.IncrementalOMSParser()
        initial = row(1) + row(2)
        c.parse(initial)
        original_parse = pnl.strict_json
        calls = []
        def trace(value):
            calls.append(value)
            return original_parse(value)
        with patch.object(pnl, "strict_json", side_effect=trace):
            events = c.parse(initial + row(3))
        self.assertEqual(calls, [row(3).decode().rstrip("\n")])
        self.assertEqual(events, [original_parse(x.decode())
                                  for x in (row(1), row(2), row(3))])
        self.assertEqual((c.rebuilds, c.appends), (1, 1))

    def test_same_size_rewrite_is_not_mistaken_for_append_or_cache_hit(self):
        c = pnl.IncrementalOMSParser()
        first = row(1)
        second = row(2)
        self.assertEqual(len(first), len(second))
        a = c.parse(first)
        b = c.parse(second)
        self.assertIsNot(a, b)
        self.assertEqual(b, [{"amount": 2}])
        self.assertEqual((c.rebuilds, c.appends, c.hits), (2, 0, 0))

    def test_truncate_and_recycle_rebuilds_exactly(self):
        c = pnl.IncrementalOMSParser()
        c.parse(row(1) + row(2) + row(3))
        self.assertEqual(c.parse(row(4)), [{"amount": 4}])
        self.assertEqual(c.parse(b""), [])
        self.assertEqual((c.rebuilds, c.appends), (3, 0))

    def test_incomplete_tail_never_poison_cached_prefix(self):
        c = pnl.IncrementalOMSParser()
        first = row(1)
        c.parse(first)
        with self.assertRaisesRegex(pnl.EvidenceError, "OMS_INCOMPLETE_TAIL"):
            c.parse(first + b'{"amount":')
        self.assertEqual(c.parse(first + row(2)),
                         [{"amount": 1}, {"amount": 2}])
        self.assertEqual((c.rebuilds, c.appends), (1, 1))

    def test_duplicate_json_key_and_nonfinite_refuse_without_poisoning(self):
        for bad in (b'{"amount":1,"amount":2}\n',
                    b'{"amount":NaN}\n'):
            with self.subTest(raw=bad):
                c = pnl.IncrementalOMSParser()
                first = row(1)
                c.parse(first)
                with self.assertRaises(pnl.EvidenceError):
                    c.parse(first + bad)
                self.assertEqual(c.parse(first + row(2)),
                                 [{"amount": 1}, {"amount": 2}])
                self.assertEqual((c.rebuilds, c.appends), (1, 1))

    def test_row_limit_applies_to_append_and_full_rebuild(self):
        with patch.object(pnl, "MAX_JOURNAL_RECORDS", 2):
            c = pnl.IncrementalOMSParser()
            first = row(1) + row(2)
            c.parse(first)
            with self.assertRaisesRegex(pnl.EvidenceError,
                                        "OMS_RECORD_BOUND_EXCEEDED"):
                c.parse(first + row(3))
            with self.assertRaisesRegex(pnl.EvidenceError,
                                        "OMS_RECORD_BOUND_EXCEEDED"):
                pnl.IncrementalOMSParser().parse(first + row(3))
            self.assertEqual(c.parse(first), [{"amount": 1}, {"amount": 2}])

    def test_cached_collection_matches_uncached_report_after_real_append(self):
        """Same accounting, fees and identity proof on both execution paths."""
        import tempfile
        from unittest.mock import patch
        from test_pnl_accounting import CollectionTests, NOW, basic
        fixtures = CollectionTests()
        with tempfile.TemporaryDirectory() as folder, patch.object(
                pnl, "now_ms", lambda: NOW):
            root = Path(folder)
            fixtures.setup_files(root)
            cache = pnl.IncrementalOMSParser()
            full = pnl.collect(fixtures.native, root / "period.json", root)
            first = pnl.collect(
                fixtures.native, root / "period.json", root, cache)
            second = pnl.collect(
                fixtures.native, root / "period.json", root, cache)
            self.assertEqual(full, first)
            self.assertEqual(first, second)
            self.assertEqual((cache.rebuilds, cache.hits), (1, 1))
            journal = root / "execution" / "oms-journal.jsonl"
            with journal.open("a") as stream:
                stream.write(json.dumps(basic()[-1]) + "\n")
            full_later = pnl.collect(
                fixtures.native, root / "period.json", root)
            cached_later = pnl.collect(
                fixtures.native, root / "period.json", root, cache)
            self.assertEqual(full_later, cached_later)
            self.assertEqual(cache.appends, 1)
            self.assertEqual(full_later["totals"]["net_total_pnl"],
                             first["totals"]["net_total_pnl"])

    def test_non_bytes_and_missing_newline_always_refuse(self):
        c = pnl.IncrementalOMSParser()
        with self.assertRaisesRegex(pnl.EvidenceError,
                                    "OMS_INPUT_TYPE_OR_SIZE"):
            c.parse(bytearray(row(1)))
        with self.assertRaisesRegex(pnl.EvidenceError,
                                    "OMS_INCOMPLETE_TAIL"):
            c.parse(b'{"amount":1}')
        self.assertEqual(c.parse(row(1)), [{"amount": 1}])


if __name__ == "__main__":
    unittest.main()
