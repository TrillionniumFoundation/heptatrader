"""Sparse END-only archives preserve live gaps and every existing source proof."""
import hashlib
import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from laya_cell import source_archive as archive
from laya_cell.dataset import verify_source_prefixes
from laya_cell.integrity import ContractError
from laya_cell.original_price_source import WINDOW_MS, read_original_price_window
from laya_cell.resident_data import read_quotes
from laya_cell.resident_recent_source import (
    read_original_quote_range, read_recent_original_quotes,
)
from laya_cell.shadow_source import canonical, digest, read_source
from laya_cell.tests import test_source_archive as source_fixture
from laya_cell.verified_sealed_readiness_cache import VerifiedSealedReadinessCache


class SparseSourceArchiveTests(unittest.TestCase):
    # Reuse genuine source-chain construction without inheriting the old tests.
    setUp = source_fixture.SourceArchiveTests.setUp
    make_segment = source_fixture.SourceArchiveTests.make_segment

    def path(self, index):
        return self.feed / f"segment-{index:08d}" / "quotes.jsonl"

    def append(self, path, kind, data, at_ms):
        rows = path.read_bytes().splitlines()
        last = json.loads(rows[-1])
        body = {"schema": "heptatrader.laya-shadow-quotes.v1",
                "sequence": len(rows), "previous": last["sha256"],
                "at_ms": at_ms, "kind": kind, "data": data}
        with path.open("ab") as stream:
            stream.write(canonical({**body, "sha256": digest(body)}) + b"\n")

    def append_quote(self, path, at_ms, price="1.125"):
        quote = {"instrument": "EUR.USD", "source": "IB", "authoritative": True,
                 "stale": False, "bid": price, "ask": "1.126",
                 "observed_at_ms": at_ms, "stale_after_ms": at_ms + 5000,
                 "subscription_id": "fixture", "subscription_state": "active"}
        self.append(path, "QUOTE", quote, at_ms + 100)

    def failure_gap(self, index=129):
        path = self.path(index)
        rows = path.read_bytes().splitlines()
        self.assertEqual(json.loads(rows[-1])["kind"], "END")
        path.write_bytes(b"\n".join(rows[:-1]) + b"\n")
        self.append(path, "FAILURE", {"reason": "QUOTE_TOOL_UNAVAILABLE"},
                    self.T + index * 30000 + 201)
        return path

    def prepare(self):
        receipt = archive.seal(self.c, 4)
        self.assertEqual(receipt["sealed_segments"], 128)
        gap = self.failure_gap()
        return gap, read_quotes(self.c, int(time.time() * 1000))

    def sparse(self):
        gap, before = self.prepare()
        receipt = archive.seal(self.c, 3)
        self.assertEqual(receipt["pages_added"], 1)
        self.assertEqual(receipt["sealed_segments"], 160)
        self.assertEqual(receipt["unsealed_segments"], 6)
        self.assertEqual(receipt["covered_segments"], 161)
        self.assertEqual(receipt["skipped_unsealed_segments"], 1)
        return gap, before, receipt

    def orphan(self):
        gap, before = self.prepare()
        with patch("laya_cell.source_archive._append_checkpoint",
                   side_effect=OSError("after-sparse-page-fsync")):
            with self.assertRaisesRegex(OSError, "after-sparse-page-fsync"):
                archive.seal(self.c, 1)
        page = archive.directory(self.c) / "page-00000004.json"
        return gap, before, page

    def test_failure_gap_preserves_rows_raw_bytes_and_v1_pages(self):
        gap, before = self.prepare()
        paths = sorted(self.feed.glob("segment-*/quotes.jsonl"))
        raw_hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        old_pages = {p: p.read_bytes() for p in archive.directory(self.c).glob("page-*.json")}
        ledger = (archive.directory(self.c) / "ledger.jsonl").read_bytes()
        receipt = archive.seal(self.c, 3)
        self.assertEqual(receipt["pages_added"], 1)
        self.assertEqual(receipt["source_files_removed"], 0)
        self.assertEqual(receipt["unsealed_segments"], 6)
        self.assertEqual({str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}, raw_hashes)
        for p, raw in old_pages.items():
            self.assertEqual(p.read_bytes(), raw)
        self.assertTrue((archive.directory(self.c) / "ledger.jsonl").read_bytes().startswith(ledger))
        page = json.loads((archive.directory(self.c) / "page-00000004.json").read_bytes())
        self.assertEqual(page["schema"], archive.SPARSE_PAGE_SCHEMA)
        self.assertEqual(page["skipped_paths"], [str(gap)])
        self.assertEqual(len(page["prefixes"]), archive.PAGE_SIZE)
        self.assertTrue(all(p["ended"] is True for p in page["prefixes"]))
        self.assertNotIn(str(gap), {p["path"] for p in page["prefixes"]})
        self.assertFalse(read_source(gap)[2]["ended"])
        self.assertEqual(json.loads(gap.read_bytes().splitlines()[-1])["kind"], "FAILURE")
        rows, pins = read_quotes(self.c, int(time.time() * 1000))
        self.assertEqual(rows, before[0])
        self.assertEqual(len(pins), 11)
        verify_source_prefixes({"source_prefixes": pins})

    def test_open_and_partial_gaps_are_not_backfilled_after_end(self):
        gap, _ = self.prepare()
        partial = self.path(130)
        rows = partial.read_bytes().splitlines()
        end = rows.pop() + b"\n"
        partial.write_bytes(b"\n".join(rows) + b"\n" + end[:20])
        receipt = archive.seal(self.c, 2)
        self.assertEqual(receipt["skipped_unsealed_segments"], 2)
        self.assertEqual(receipt["covered_segments"], 162)
        self.assertGreater(read_source(partial)[2]["uncommitted_tail_bytes"], 0)
        self.append(gap, "END", {}, self.T + 129 * 30000 + 300)
        with partial.open("ab") as stream:
            stream.write(end[20:])
        for i in range(166, 195):
            self.make_segment(i)
        receipt = archive.seal(self.c, 2)
        self.assertEqual(receipt["pages_added"], 1)
        self.assertEqual(receipt["sealed_segments"], 192)
        self.assertEqual(receipt["unsealed_segments"], 3)
        sealed, _ = archive.inventory(self.c, sorted(self.feed.glob("segment-*/quotes.jsonl")))
        self.assertFalse({str(gap), str(partial)} & {p["path"] for p in sealed})
        _, pins = read_quotes(self.c, int(time.time() * 1000))
        raw = {p["path"]: p for p in pins if p.get("schema") != archive.PIN_SCHEMA}
        self.assertTrue(raw[str(gap)]["ended"])
        self.assertTrue(raw[str(partial)]["ended"])
        verify_source_prefixes({"source_prefixes": pins})

    def test_missing_gap_or_inserted_path_cannot_change_covered_inventory(self):
        gap, _, _ = self.sparse()
        raw = gap.read_bytes()
        gap.unlink()
        with self.assertRaisesRegex(ContractError, "SOURCE_ARCHIVE_REORDERED_OR_MISSING"):
            read_quotes(self.c, int(time.time() * 1000))
        gap.write_bytes(raw)
        gap.chmod(0o600)
        inserted = self.feed / "segment-00000129a"
        inserted.mkdir(mode=0o700)
        (inserted / "quotes.jsonl").write_bytes(raw)
        (inserted / "quotes.jsonl").chmod(0o600)
        with self.assertRaisesRegex(ContractError, "SOURCE_ARCHIVE_REORDERED_OR_MISSING"):
            read_quotes(self.c, int(time.time() * 1000))

    def test_dataset_needs_gap_pin_only_for_its_frozen_sparse_pages(self):
        gap, before, _ = self.sparse()
        old_pages = [p for p in before[1] if p.get("schema") == archive.PIN_SCHEMA]
        self.assertEqual(archive.verify_archive_pin_sequence(old_pages), set())
        verify_source_prefixes({"source_prefixes": old_pages})
        verify_source_prefixes({"source_prefixes": before[1]})
        _, pins = read_quotes(self.c, int(time.time() * 1000))
        self.assertEqual(archive.verify_archive_pin_sequence(pins[:5]), {str(gap)})
        omitted = [p for p in pins if p["path"] != str(gap)]
        with self.assertRaisesRegex(ContractError, "SKIPPED_RAW_PROOF_MISSING"):
            verify_source_prefixes({"source_prefixes": omitted})
        self.append(gap, "END", {}, self.T + 129 * 30000 + 300)
        verify_source_prefixes({"source_prefixes": pins})

    def test_gap_counts_towards_unsealed_bound(self):
        self.sparse()
        for i in range(166, 288):
            self.make_segment(i)
        paths = sorted(self.feed.glob("segment-*/quotes.jsonl"))
        sealed, _ = archive.inventory_index(self.c, paths)
        self.assertEqual(len(paths) - len(sealed), archive.UNSEALED_BOUND)
        self.make_segment(288)
        with self.assertRaisesRegex(ContractError, "SOURCE_UNSEALED_SEGMENT_BOUND"):
            archive.inventory_index(self.c, sorted(self.feed.glob("segment-*/quotes.jsonl")))

    def test_invalid_gap_chain_is_not_silently_skipped(self):
        gap, _ = self.prepare()
        gap.write_bytes(gap.read_bytes().replace(b"1.125", b"1.124"))
        with self.assertRaisesRegex(ValueError, "SHADOW_SOURCE_CHAIN"):
            archive.seal(self.c, 1)
        self.assertEqual(len(list(archive.directory(self.c).glob("page-*.json"))), 4)

    def test_recent_range_and_price_readers_keep_recent_gap_quotes(self):
        gap, _, _ = self.sparse()
        now = self.T + 10000000
        for stamp in range(now - 600000, now, 100000):
            self.append_quote(self.path(165), stamp)
        gap_stamp = now - 450000
        self.append_quote(gap, gap_stamp)
        full, full_pins = read_quotes(self.c, now)
        recent, recent_pins = read_recent_original_quotes(self.c, now)
        self.assertIn(gap_stamp, {q["t"] for q in recent})
        self.assertEqual(recent_pins, full_pins)
        self.assertEqual([q for q in recent if q["t"] >= now - 360000],
                         [q for q in full if q["t"] >= now - 360000])
        first, last = now - 600000, now - 250000
        selected, range_pins = read_original_quote_range(self.c, now, first, last)
        expected = [q for q in full if first <= q["t"] <= last]
        expected += [next(q for q in full if q["t"] > last)]
        self.assertEqual(selected, expected)
        self.assertEqual(range_pins, full_pins)
        prices, proof = read_original_price_window(self.c, now)
        self.assertEqual(prices, [q for q in full if now - WINDOW_MS <= q["t"] <= now])
        self.assertIn(str(gap), {p["path"] for p in proof["selected_original_prefixes"]})

    def test_cached_full_and_warm_recent_reads_keep_gap(self):
        gap, _, _ = self.sparse()
        now, first = self.T + 10000000, self.T + 4900000
        cache = VerifiedSealedReadinessCache()
        self.addCleanup(cache.close)
        full, pins = read_quotes(self.c, now)
        self.assertEqual(cache.read_quotes(self.c, now), (full, pins))
        expected = [q for q in full if q["t"] >= first]
        self.assertEqual(cache.read_quotes(self.c, now, earliest_ms=first), (expected, pins))
        self.assertEqual(cache.read_quotes(self.c, now, earliest_ms=first), (expected, pins))
        self.assertNotIn(str(gap), cache._history["retired"])
        before = cache.counters()["historical_semantic_full_fallbacks"]
        self.append_quote(gap, now - 50000)
        full, pins = read_quotes(self.c, now)
        self.assertEqual(cache.read_quotes(self.c, now, earliest_ms=first),
                         ([q for q in full if q["t"] >= first], pins))
        # The warm pass already proved the old overlap. A purely recent
        # append may retain that proof while still parsing the live gap.
        self.assertEqual(cache.counters()["historical_semantic_full_fallbacks"], before)

    def test_cached_gap_append_cannot_hide_a_retired_price_conflict(self):
        gap, _, _ = self.sparse()
        now, first = self.T + 10000000, self.T + 4900000
        cache = VerifiedSealedReadinessCache()
        self.addCleanup(cache.close)
        cache.read_quotes(self.c, now, earliest_ms=first)
        cache.read_quotes(self.c, now, earliest_ms=first)
        self.append_quote(gap, self.T + 150 * 30000 + 100, price="1.124")
        with self.assertRaisesRegex(ContractError, "CROSS_SEGMENT_PRICE_CONFLICT"):
            cache.read_quotes(self.c, now, earliest_ms=first)

    def test_health_proof_cache_still_reparses_the_unsealed_gap(self):
        gap, _, _ = self.sparse()
        now, first = self.T + 10000000, self.T + 4900000
        cache = VerifiedSealedReadinessCache(original_proof_reverify_ms=300000)
        self.addCleanup(cache.close)
        full, pins = read_quotes(self.c, now)
        expected = ([q for q in full if q["t"] >= first], pins)
        self.assertEqual(cache.read_quotes(self.c, now, earliest_ms=first), expected)
        self.assertEqual(cache.read_quotes(self.c, now, earliest_ms=first), expected)
        self.assertGreaterEqual(cache.counters()["original_proof_full_sha256_verifications"], 160)
        gap.write_bytes(gap.read_bytes().replace(b"1.125", b"1.124"))
        with self.assertRaisesRegex(ValueError, "SHADOW_SOURCE_CHAIN"):
            cache.read_quotes(self.c, now, earliest_ms=first)

    def test_sparse_orphan_recovers_after_gap_completion_without_rewriting_page(self):
        gap, _, page = self.orphan()
        raw = page.read_bytes()
        with self.assertRaisesRegex(ContractError, "PAGE_CHECKPOINT_COUNT"):
            read_quotes(self.c, int(time.time() * 1000))
        self.append(gap, "END", {}, self.T + 129 * 30000 + 300)
        receipt = archive.seal(self.c, 1)
        self.assertEqual(receipt["recovered_fsynced_orphan_pages"], 1)
        self.assertEqual(page.read_bytes(), raw)
        rows, pins = read_quotes(self.c, int(time.time() * 1000))
        self.assertEqual(len(rows), 166)
        self.assertTrue(next(p for p in pins if p["path"] == str(gap))["ended"])
        verify_source_prefixes({"source_prefixes": pins})

    def test_sparse_orphan_refuses_missing_gap_and_changed_sealed_original(self):
        gap, _, _ = self.orphan()
        ledger = archive.directory(self.c) / "ledger.jsonl"
        original_ledger = ledger.read_bytes()
        raw = gap.read_bytes()
        gap.unlink()
        with self.assertRaisesRegex(ContractError, "ORPHAN_SOURCE_REORDERED"):
            archive.seal(self.c, 1)
        gap.write_bytes(raw)
        gap.chmod(0o600)
        source = self.path(130)
        source.write_bytes(source.read_bytes().replace(b"1.125", b"1.124"))
        with self.assertRaisesRegex(ValueError, "SHADOW_SOURCE_CHAIN"):
            archive.seal(self.c, 1)
        self.assertEqual(ledger.read_bytes(), original_ledger)

    def test_sparse_orphan_refuses_unbound_or_overlapping_gap_list(self):
        gap, _, page = self.orphan()
        doc = json.loads(page.read_bytes())
        for name, replacement in (
            ("omitted", []),
            ("other-path", [str(self.path(165))]),
            ("overlap", [doc["prefixes"][0]["path"]]),
            ("duplicate", [str(gap), str(gap)]),
        ):
            with self.subTest(case=name):
                forged = {**doc, "skipped_paths": replacement}
                page.write_bytes(canonical(forged) + b"\n")
                with self.assertRaises(ContractError):
                    archive.seal(self.c, 1)
        page.write_bytes(canonical({**doc, "schema": "unknown"}) + b"\n")
        with self.assertRaisesRegex(ContractError, "PAGE_INVENTORY"):
            archive.seal(self.c, 1)
        self.assertEqual(len((archive.directory(self.c) / "ledger.jsonl").read_bytes().splitlines()), 4)


if __name__ == "__main__":
    unittest.main()
