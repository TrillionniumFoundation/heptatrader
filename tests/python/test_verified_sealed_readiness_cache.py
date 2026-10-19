"""Negative contract tests for strictly verified sealed-quote decoder caching."""
from __future__ import annotations
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from laya_cell.integrity import ContractError
from laya_cell import source_readiness as monitor
from laya_cell.verified_sealed_readiness_cache import VerifiedSealedReadinessCache
from laya_cell import verified_sealed_readiness_cache as module

class Harness:
    def __init__(self, root):
        self.feed=Path(root)/"feed"
        self.feed.mkdir(mode=0o700)
        self.period=Path(root)/"period.json"
        self.period.write_bytes(b'{"domain":"PAPER"}')
        a=self.feed/"segment-001";a.mkdir(mode=0o700)
        b=self.feed/"segment-002";b.mkdir(mode=0o700)
        self.p1=a/"quotes.jsonl";self.p2=b/"quotes.jsonl"
        self.p1.write_bytes(b"sealed source contents\n")
        self.p2.write_bytes(b"live tail one\n")
        self.p1.chmod(0o600);self.p2.chmod(0o600)
        period_sha=hashlib.sha256(self.period.read_bytes()).hexdigest()
        self.config={"period_path":str(self.period),
            "period_sha256":period_sha,
            "feed_directory":str(self.feed),
            "completed_history_sources":[]}
        self.pin={"path":str(self.p1),
                  "sha256":hashlib.sha256(self.p1.read_bytes()).hexdigest(),
                  "bytes":len(self.p1.read_bytes()),"ended":True}
        self.inventory_calls=0;self.parses=[]
        self.headers={"period_sha256":period_sha}
        self.pinned_pages=[{"page_sha256":"a"*64,"index":0}]
    def inventory(self,c,paths):
        self.inventory_calls+=1
        assert paths==[self.p1,self.p2]
        raw=self.p1.read_bytes()
        if (len(raw)!=self.pin["bytes"] or
                hashlib.sha256(raw).hexdigest()!=self.pin["sha256"]):
            raise ContractError("SOURCE_PREFIX_HASH_CHANGED")
        return [self.pin.copy()],self.pinned_pages.copy()
    def read_source(self,path):
        self.parses.append(str(path))
        if path==self.p1:
            return self.headers.copy(),[
                {"t":1000,"known_at_ms":1030,"bid":"1.1000","ask":"1.1002"}], self.pin.copy()
        return self.headers.copy(),[
            {"t":2000,"known_at_ms":2040,"bid":"1.2000","ask":"1.2002"}], {"path":str(self.p2),"bytes":self.p2.stat().st_size,
             "sha256":hashlib.sha256(self.p2.read_bytes()).hexdigest(),
             "uncommitted_tail_bytes":0,"ended":False}
    def patched(self):
        from contextlib import ExitStack
        stack=ExitStack()
        stack.enter_context(patch.object(module,"inventory",self.inventory))
        stack.enter_context(patch.object(module,"read_source",self.read_source))
        return stack

class CacheTests(unittest.TestCase):
    def new(self):
        tmp=tempfile.TemporaryDirectory()
        return tmp,Harness(tmp.name),VerifiedSealedReadinessCache()
    def test_sha256_inventory_rechecked_on_every_cache_hit(self):
        tmp,h,cache=self.new()
        with tmp,h.patched():
            cold=cache.read_quotes(h.config,3000)
            hot=cache.read_quotes(h.config,3000)
            self.assertEqual(cold,hot)
            self.assertEqual(h.inventory_calls,2)
            self.assertEqual(cache.sealed_hits,1)
            self.assertEqual(cache.sealed_misses,1)
            self.assertEqual(cache.unsealed_reparsed,2)
            self.assertEqual(len(h.parses),3)
            self.assertEqual(len(hot[0]),2)
    def test_same_size_sealed_mutation_refused_before_reuse(self):
        tmp,h,cache=self.new()
        with tmp,h.patched():
            cache.read_quotes(h.config,3000)
            original=h.p1.read_bytes()
            h.p1.write_bytes(b"X"+original[1:])
            self.assertEqual(h.p1.stat().st_size,len(original))
            with self.assertRaisesRegex(ContractError,"SOURCE_PREFIX_HASH_CHANGED"):
                cache.read_quotes(h.config,3000)
    def test_unsealed_always_reparsed_even_if_full_archive_cached(self):
        tmp,h,cache=self.new()
        with tmp,h.patched():
            cache.read_quotes(h.config,3000)
            h.p2.write_bytes(b"live tail two\n")
            _,pins=cache.read_quotes(h.config,3000)
            self.assertEqual(cache.unsealed_reparsed,2)
            self.assertEqual(pins[-1]["sha256"],
                hashlib.sha256(b"live tail two\n").hexdigest())
    def test_sealed_receipt_mismatch_refused(self):
        tmp,h,cache=self.new()
        with tmp,h.patched():
            cache.read_quotes(h.config,3000)
            h.pin["sha256"]="f"*64
            with self.assertRaisesRegex(ContractError,"SOURCE_PREFIX_HASH_CHANGED"):
                cache.read_quotes(h.config,3000)
    def test_changed_config_fails_closed(self):
        tmp,h,cache=self.new()
        with tmp,h.patched():
            cache.read_quotes(h.config,3000)
            h.config["completed_history_sources"]=["fake"]
            with self.assertRaisesRegex(ContractError,"READINESS_CACHE_CONFIG_CHANGED"):
                cache.read_quotes(h.config,3000)
    def test_changed_period_source_fails_closed(self):
        tmp,h,cache=self.new()
        with tmp,h.patched():
            cache.read_quotes(h.config,3000)
            h.period.write_text('{"domain":"LIVE"}')
            with self.assertRaisesRegex(ContractError,"RESIDENT_PERIOD_CHANGED"):
                cache.read_quotes(h.config,3000)
    def test_backward_wallclock_refuses_trusting_old_decode(self):
        tmp,h,cache=self.new()
        with tmp,h.patched():
            cache.read_quotes(h.config,3000)
            with patch.object(module.time,"time",return_value=1.):
                with self.assertRaisesRegex(ContractError,"READINESS_CACHE_CLOCK_REGRESSED"):
                    cache.read_quotes(h.config,3000)
    def test_asof_only_includes_causally_available_original(self):
        tmp,h,cache=self.new()
        with tmp,h.patched():
            cache.read_quotes(h.config,3000)
            rows,pins=cache.read_quotes(h.config,1500)
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]["t"],1000)
    def test_archive_page_inventory_proofs_kept_in_original_order(self):
        tmp,h,cache=self.new()
        with tmp,h.patched():
            cache.read_quotes(h.config,3000)
            h.pinned_pages.append({"page_sha256":"b"*64,"index":1})
            _,pins=cache.read_quotes(h.config,3000)
            self.assertEqual(len(pins),3)
            self.assertEqual(pins[0]["page_sha256"],"a"*64)
            self.assertEqual(pins[1]["page_sha256"],"b"*64)
    def test_memory_cap_eviction_does_not_change_read_success(self):
        tmp,h,cache=self.new()
        with tmp,h.patched(),patch.object(module,"MAX_CACHED_QUOTE_RECORDS",0):
            r,pins=cache.read_quotes(h.config,3000)
            self.assertEqual(len(r),2)
            self.assertEqual(cache.counters()["cached_sealed_segments"],0)
    def test_oversized_archive_retains_hot_suffix_without_lru_scan_thrash(self):
        """Over-cap ascending inventories must not evict the next cached key."""
        for max_segments, max_records in ((2, 99), (99, 2)):
            with self.subTest(max_segments=max_segments,max_records=max_records):
                tmp,h,cache=self.new()
                with tmp:
                    paths=[h.p1,h.p2]
                    for n in range(3,6):
                        folder=h.feed/f"segment-{n:03d}"
                        folder.mkdir(mode=0o700)
                        path=folder/"quotes.jsonl"
                        path.write_bytes(f"original sealed {n}\n".encode())
                        path.chmod(0o600)
                        paths.append(path)
                    pins=[{"path":str(path),"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
                           "bytes":path.stat().st_size,"ended":True} for path in paths]
                    parses=[]; inventory_calls=[]
                    def checked_inventory(config,files):
                        self.assertEqual(files,paths)
                        inventory_calls.append(1)
                        for path,pin in zip(paths,pins):
                            if hashlib.sha256(path.read_bytes()).hexdigest()!=pin["sha256"]:
                                raise ContractError("SOURCE_PREFIX_HASH_CHANGED")
                        return [pin.copy() for pin in pins],[]
                    def strict_reader(path):
                        parses.append(path)
                        index=paths.index(path)
                        return h.headers.copy(),[{"t":1000*(index+1),
                           "known_at_ms":1000*(index+1)+30,"bid":"1.1000","ask":"1.1002"}],pins[index].copy()
                    with patch.object(module,"inventory",checked_inventory),patch.object(module,"read_source",strict_reader),\
                         patch.object(module,"MAX_CACHED_SEGMENTS",max_segments),\
                         patch.object(module,"MAX_CACHED_QUOTE_RECORDS",max_records):
                        cold=cache.read_quotes(h.config,10000)
                        self.assertEqual(len(parses),5)
                        self.assertEqual(cache.counters()["cached_sealed_segments"],2)
                        hot=cache.read_quotes(h.config,10000)
                        self.assertEqual(hot,cold)
                        self.assertEqual(len(parses),8)  # oldest three strictly parsed again
                        self.assertEqual(cache.sealed_hits,2)
                        self.assertEqual(cache.sealed_hits_bytes_reverified_at_use,2)
                        cache.read_quotes(h.config,10000)
                        self.assertEqual(len(parses),11)
                        self.assertEqual(cache.sealed_hits,4)
                        self.assertEqual(len(inventory_calls),3)
                        paths[0].write_bytes(b"X"+paths[0].read_bytes()[1:])
                        with self.assertRaisesRegex(ContractError,"SOURCE_PREFIX_HASH_CHANGED"):
                            cache.read_quotes(h.config,10000)

    def test_counters_mark_research_not_broker_signature(self):
        tmp,h,cache=self.new()
        with tmp,h.patched():
            cache.read_quotes(h.config,3000)
            c=cache.counters()
            self.assertIs(c["trade_authority"],False)
            self.assertIs(c["cache_is_not_independent_broker_signature"],True)
            self.assertEqual(c["broker_orders_sent"],0)
    def test_bad_timestamp_type_fails_closed(self):
        tmp,h,cache=self.new()
        with tmp,h.patched():
            for v in (True,0,-1):
                with self.subTest(v=v),self.assertRaisesRegex(ContractError,"READINESS_CACHE_CLOCK_INVALID"):
                    cache.read_quotes(h.config,v)
    def test_cached_flag_requires_explicit_sandbox(self):
        argv=["readiness","--config","/invalid","--output","/invalid",
              "--verified-sealed-cache"]
        with patch.object(sys,"argv",argv),self.assertRaises(SystemExit) as ctx:
            monitor.main()
        self.assertEqual(ctx.exception.code,2)
    def test_post_inventory_pre_cache_hit_same_size_mutation_refused(self):
        """The file must be rehashed at each cache HIT, not just inventory."""
        tmp,h,cache=self.new()
        with tmp,h.patched():
            cache.read_quotes(h.config,3000)
            pristine=h.p1.read_bytes()
            verified_inventory=h.inventory
            def verify_then_swap(config,paths):
                pinned=verified_inventory(config,paths)
                # Adversary races immediately after a successful inventory.
                h.p1.write_bytes(b"Z"+pristine[1:])
                return pinned
            with patch.object(module,"inventory",verify_then_swap):
                with self.assertRaisesRegex(ContractError,"SOURCE_PREFIX_HASH_CHANGED"):
                    cache.read_quotes(h.config,3000)
            self.assertEqual(cache.sealed_hits,0)

    def test_unsealed_segment_promotion_to_archived_pin_is_reparsed(self):
        tmp,h,cache=self.new()
        with tmp,h.patched():
            cache.read_quotes(h.config,3000)
            _,_,pin2=h.read_source(h.p2)
            pin2["ended"]=True
            def newly_archived_inventory(config,paths):
                h.inventory(config,paths)
                if hashlib.sha256(h.p2.read_bytes()).hexdigest()!=pin2["sha256"]:
                    raise ContractError("SOURCE_PREFIX_HASH_CHANGED")
                return [h.pin.copy(),pin2.copy()],h.pinned_pages.copy()
            original_reader=h.read_source
            def newly_archived_reader(path):
                header,rows,prefix=original_reader(path)
                if path==h.p2:
                    prefix["ended"]=True
                return header,rows,prefix
            with patch.object(module,"inventory",newly_archived_inventory):
                with patch.object(module,"read_source",newly_archived_reader):
                    cache.read_quotes(h.config,3000)
                    cache.read_quotes(h.config,3000)
            self.assertEqual(cache.sealed_misses,2)
            self.assertGreaterEqual(cache.sealed_hits,3)
            self.assertEqual(cache.counters()["cached_sealed_segments"],2)
            self.assertEqual(cache.sealed_hits_bytes_reverified_at_use,cache.sealed_hits)

    def test_original_monitor_rules_unchanged(self):
        r=monitor.RULE
        self.assertEqual((r["input_probes"],r["minimum_usable"],
                          r["maximum_missing"],r["source_ttl_ms"],
                          r["path_gap_max_ms"],r["endpoint_tolerance_ms"]),
                         (24,20,4,5000,30000,5000))
    def test_original_assess_default_still_uses_canonical_reader(self):
        c={"period_sha256":"f"*64}
        q=[{"t":1000,"known_at_ms":1000,"bid":1,"ask":1}]
        with patch.object(monitor,"read_quotes",return_value=(q,[])) as rd:
            with patch.object(monitor,"probe",return_value={"status":"USABLE"}):
                report=monitor.assess(c,now=1200)
        self.assertEqual(report["usable"],24)
        rd.assert_called_once_with(c,1200)


class RecentHarness(Harness):
    def __init__(self, root):
        super().__init__(root)
        self.rows = {
            self.p1: [{"t": 1000, "known_at_ms": 1030, "bid": "1.1000", "ask": "1.1002", "source": "sealed"}],
            self.p2: [{"t": 2000, "known_at_ms": 2040, "bid": "1.2000", "ask": "1.2002", "source": "tail"}],
        }
        self.sealed = [self.pin]

    def index(self, config, paths):
        assert paths == [self.p1, self.p2]
        return [dict(pin) for pin in self.sealed], list(self.pinned_pages)

    def inventory(self, config, paths):
        self.inventory_calls += 1
        pins, pages = self.index(config, paths)
        for pin in pins:
            module.verify_prefix(pin, sealed=True)
        return pins, pages

    def seal_tail(self):
        self.sealed.append({"path": str(self.p2), "bytes": self.p2.stat().st_size,
                            "sha256": hashlib.sha256(self.p2.read_bytes()).hexdigest(),
                            "ended": True})

    def read_source(self, path):
        header, _, prefix = super().read_source(path)
        prefix = next((dict(pin) for pin in self.sealed if pin["path"] == str(path)), prefix)
        return header, [dict(row) for row in self.rows[path]], prefix

    def patched(self):
        stack = super().patched()
        stack.enter_context(patch.object(module, "inventory_index", self.index))
        return stack


class RecentViewTests(unittest.TestCase):
    def new(self):
        tmp = tempfile.TemporaryDirectory()
        return tmp, RecentHarness(tmp.name), VerifiedSealedReadinessCache()

    def test_uncached_old_segments_skip_decode_but_preserve_result_and_receipts(self):
        tmp, h, cache = self.new()
        with tmp, h.patched(), patch.object(module, "MAX_CACHED_QUOTE_RECORDS", 0):
            all_rows, all_pins = VerifiedSealedReadinessCache().read_quotes(h.config, 3000)
            h.parses.clear()
            cold = cache.read_quotes(h.config, 3000, earliest_ms=1500)
            hot = cache.read_quotes(h.config, 3000, earliest_ms=1500)
            self.assertEqual(cold, ([q for q in all_rows if q["t"] >= 1500], all_pins))
            self.assertEqual(hot, cold)
            self.assertEqual(h.parses.count(str(h.p1)), 1)
            self.assertEqual(h.parses.count(str(h.p2)), 2)
            self.assertEqual(cache.retired_segments_reverified, 1)
            self.assertEqual(cache.counters()["cached_original_quote_records"], 0)

    def test_retired_same_size_mutation_is_rejected_at_consumption(self):
        tmp, h, cache = self.new()
        with tmp, h.patched():
            cache.read_quotes(h.config, 3000, earliest_ms=1500)
            raw = h.p1.read_bytes()
            h.p1.write_bytes(b"X" + raw[1:])
            with self.assertRaisesRegex(ContractError, "SOURCE_PREFIX_HASH_CHANGED"):
                cache.read_quotes(h.config, 3000, earliest_ms=1500)
            self.assertEqual(cache.history_reused, 0)

    def test_changed_tail_with_late_old_conflict_requires_complete_merge(self):
        tmp, h, cache = self.new()
        with tmp, h.patched():
            cache.read_quotes(h.config, 3000, earliest_ms=1500)
            h.rows[h.p2].append({"t": 1000, "known_at_ms": 2900, "bid": "9", "ask": "10"})
            h.p2.write_bytes(h.p2.read_bytes() + b"late original row\n")
            with self.assertRaisesRegex(ContractError, "CROSS_SEGMENT_PRICE_CONFLICT"):
                cache.read_quotes(h.config, 3000, earliest_ms=1500)
            self.assertIsNone(cache._history)
            self.assertEqual(cache.history_fallbacks, 1)

    def test_unchanged_tail_future_known_overlap_is_not_a_prior_proof(self):
        tmp, h, cache = self.new()
        with tmp, h.patched():
            h.rows[h.p2].append({"t": 1000, "known_at_ms": 3500, "bid": "9", "ask": "10"})
            cache.read_quotes(h.config, 3000, earliest_ms=1500)
            with self.assertRaisesRegex(ContractError, "CROSS_SEGMENT_PRICE_CONFLICT"):
                cache.read_quotes(h.config, 4000, earliest_ms=1500)
            self.assertIsNone(cache._history)
            self.assertEqual(cache.history_fallbacks, 1)

    def test_unseen_sealed_rows_are_not_retired(self):
        tmp, h, cache = self.new()
        with tmp, h.patched():
            h.rows[h.p1][0]["known_at_ms"] = 3500
            cache.read_quotes(h.config, 3000, earliest_ms=1500)
            self.assertIsNone(cache._history)
            cache.read_quotes(h.config, 4000, earliest_ms=1500)
            self.assertIsNotNone(cache._history)

    def test_backward_asof_or_wider_window_rebuilds_the_original_population(self):
        for at_ms, cutoff in ((1500, 1200), (3000, 500)):
            with self.subTest(at_ms=at_ms, cutoff=cutoff):
                tmp, h, cache = self.new()
                with tmp, h.patched():
                    cache.read_quotes(h.config, 3000, earliest_ms=1500)
                    actual, pins = cache.read_quotes(h.config, at_ms, earliest_ms=cutoff)
                    full, expected_pins = VerifiedSealedReadinessCache().read_quotes(h.config, at_ms)
                    expected = [q for q in full if q["t"] >= cutoff] or full[-1:]
                    self.assertEqual((actual, pins), (expected, expected_pins))
                    self.assertEqual(cache.history_fallbacks, 1)

    def test_cutoff_boundary_is_included_and_not_retired(self):
        tmp, h, cache = self.new()
        with tmp, h.patched():
            rows, _ = cache.read_quotes(h.config, 3000, earliest_ms=1000)
            self.assertEqual([q["t"] for q in rows], [1000, 2000])
            self.assertIsNone(cache._history)

    def test_latest_old_quote_remains_bound_to_original_retired_source(self):
        tmp, h, cache = self.new()
        with tmp, h.patched():
            h.rows[h.p1][0]["known_at_ms"] = 1150
            h.rows[h.p2] = [dict(h.rows[h.p1][0], known_at_ms=1120, source="tail")]
            cold, _ = cache.read_quotes(h.config, 3000, earliest_ms=2000)
            self.assertEqual(cold[0]["source"], "tail")
            h.rows[h.p2] = [{"t": 4000, "known_at_ms": 4500, "bid": "2", "ask": "3"}]
            h.p2.write_bytes(b"changed active original\n")
            hot = cache.read_quotes(h.config, 3000, earliest_ms=2000)
            full, pins = VerifiedSealedReadinessCache().read_quotes(h.config, 3000)
            self.assertEqual(hot, (full[-1:], pins))
            self.assertEqual(hot[0][0]["known_at_ms"], 1150)
            self.assertEqual(hot[0][0]["source"], "sealed")
            self.assertEqual(cache.history_fallbacks, 0)

    def test_retired_equal_timestamp_ties_keep_original_segment_order(self):
        tmp, h, cache = self.new()
        with tmp, h.patched():
            h.rows[h.p2] = [dict(h.rows[h.p1][0], source="second")]
            h.seal_tail()
            first = cache.read_quotes(h.config, 3000, earliest_ms=2000)
            second = cache.read_quotes(h.config, 3000, earliest_ms=2000)
            self.assertEqual(first, second)
            self.assertEqual(second[0][0]["source"], "sealed")

    def test_new_sealed_inventory_rebuilds_before_retiring_more_sources(self):
        tmp, h, cache = self.new()
        with tmp, h.patched():
            cache.read_quotes(h.config, 3000, earliest_ms=2500)
            h.seal_tail()
            recent = cache.read_quotes(h.config, 3000, earliest_ms=2500)
            full, pins = VerifiedSealedReadinessCache().read_quotes(h.config, 3000)
            self.assertEqual(recent, (full[-1:], pins))
            self.assertEqual(cache.history_fallbacks, 1)
            self.assertEqual(cache.counters()["retired_decoded_segments"], 2)

    def test_returned_rows_do_not_mutate_historical_conclusion(self):
        tmp, h, cache = self.new()
        with tmp, h.patched():
            h.rows[h.p2] = []
            first, _ = cache.read_quotes(h.config, 3000, earliest_ms=2000)
            first[0]["bid"] = 99
            first[0]["known_at_ms"] = 1
            second, _ = cache.read_quotes(h.config, 3000, earliest_ms=2000)
            self.assertEqual(second[0]["bid"], 1.1)
            self.assertEqual(second[0]["known_at_ms"], 1030)

    def test_failed_result_construction_does_not_publish_new_history(self):
        tmp, h, cache = self.new()
        with tmp, h.patched():
            with patch.object(module, "float", side_effect=ValueError("conversion"), create=True):
                with self.assertRaisesRegex(ValueError, "conversion"):
                    cache.read_quotes(h.config, 3000, earliest_ms=1500)
            self.assertIsNone(cache._history)
            cache.read_quotes(h.config, 3000, earliest_ms=1500)
            self.assertIsNotNone(cache._history)

    def test_no_causally_available_quote_stays_empty(self):
        tmp, h, cache = self.new()
        with tmp, h.patched():
            h.rows[h.p1][0]["known_at_ms"] = 3500
            h.rows[h.p2][0]["known_at_ms"] = 4000
            self.assertEqual(cache.read_quotes(h.config, 3000, earliest_ms=1500)[0], [])

    def test_new_quotes_end_and_new_segment_do_not_reparse_retired_history(self):
        tmp, h, cache = self.new()
        h.rows[h.p2].insert(0, dict(h.rows[h.p1][0], source="tail"))
        recovery = h.feed / "segment-recovery-live"
        recovery.mkdir(mode=0o700)
        recovery_source = recovery / "quotes.jsonl"
        recovery_source.write_bytes(b"original recovery source\n")
        recovery_source.chmod(0o600)
        h.rows[recovery_source] = [dict(h.rows[h.p1][0], t=500, known_at_ms=530)]
        ended = {recovery_source}
        def index(config, paths):
            self.assertEqual(paths, sorted(h.rows))
            return [dict(pin) for pin in h.sealed], list(h.pinned_pages)
        def reader(path):
            h.parses.append(str(path))
            prefix = next((dict(pin) for pin in h.sealed if pin["path"] == str(path)), None)
            if prefix is None:
                raw = path.read_bytes()
                prefix = {"path": str(path), "bytes": len(raw),
                          "sha256": hashlib.sha256(raw).hexdigest(),
                          "uncommitted_tail_bytes": 0, "ended": path in ended}
            return h.headers.copy(), [dict(row) for row in h.rows[path]], prefix
        with tmp, patch.object(h, "index", index), patch.object(h, "read_source", reader), h.patched():
            cache.read_quotes(h.config, 3000, earliest_ms=1500)
            for change in ("new_quote", "end", "new_segment"):
                with self.subTest(change=change):
                    if change == "new_quote":
                        h.rows[h.p2].append({"t": 3000, "known_at_ms": 3040, "bid": "2", "ask": "3"})
                        h.p2.write_bytes(h.p2.read_bytes() + b"new quote\n")
                    elif change == "end":
                        ended.add(h.p2)
                        h.p2.write_bytes(h.p2.read_bytes() + b"END\n")
                    else:
                        folder = h.feed / "segment-003"
                        folder.mkdir(mode=0o700)
                        path = folder / "quotes.jsonl"
                        path.write_bytes(b"next original segment\n")
                        path.chmod(0o600)
                        h.rows[path] = [{"t": 4000, "known_at_ms": 4040, "bid": "3", "ask": "4"}]
                    actual = cache.read_quotes(h.config, 5000, earliest_ms=1500)
                    full, pins = VerifiedSealedReadinessCache().read_quotes(h.config, 5000)
                    self.assertEqual(actual, ([row for row in full if row["t"] >= 1500], pins))
                    self.assertEqual(cache.sealed_misses, 1)
                    self.assertEqual(cache.history_fallbacks, 0)

    def test_changed_old_semantics_still_require_complete_history(self):
        for change in ("old_price", "boundary_price", "known_at", "receipt", "delete", "reorder", "insert"):
            with self.subTest(change=change):
                tmp, h, cache = self.new()
                h.rows[h.p1].insert(0, dict(h.rows[h.p1][0], t=900, known_at_ms=930))
                h.rows[h.p2][:0] = [dict(row, source="tail") for row in h.rows[h.p1]]
                with tmp, h.patched():
                    cache.read_quotes(h.config, 3000, earliest_ms=1500)
                    if change in ("old_price", "boundary_price"):
                        h.rows[h.p2][change == "boundary_price"].update(bid="9", ask="10")
                    elif change == "known_at":
                        h.rows[h.p2][0]["known_at_ms"] = 940
                    elif change == "receipt":
                        h.rows[h.p2][0]["receipt"] = "f" * 64
                    elif change == "delete":
                        h.rows[h.p2].pop(0)
                    elif change == "reorder":
                        h.rows[h.p2][:2] = reversed(h.rows[h.p2][:2])
                    else:
                        h.rows[h.p2].insert(1, dict(h.rows[h.p2][0], t=950, known_at_ms=980))
                    h.p2.write_bytes(h.p2.read_bytes() + b"changed historical semantics\n")
                    if change in ("old_price", "boundary_price"):
                        with self.assertRaisesRegex(ContractError, "CROSS_SEGMENT_PRICE_CONFLICT"):
                            cache.read_quotes(h.config, 4000, earliest_ms=1500)
                        with self.assertRaisesRegex(ContractError, "CROSS_SEGMENT_PRICE_CONFLICT"):
                            VerifiedSealedReadinessCache().read_quotes(h.config, 4000)
                    else:
                        actual = cache.read_quotes(h.config, 4000, earliest_ms=1500)
                        full, pins = VerifiedSealedReadinessCache().read_quotes(h.config, 4000)
                        self.assertEqual(actual, ([row for row in full if row["t"] >= 1500], pins))
                    self.assertEqual(cache.history_fallbacks, 1)

    def test_appended_prefix_cannot_reuse_old_rows_that_only_now_become_visible(self):
        tmp, h, cache = self.new()
        with tmp, h.patched():
            h.rows[h.p2].append({"t": 1000, "known_at_ms": 3500, "bid": "9", "ask": "10"})
            cache.read_quotes(h.config, 3000, earliest_ms=1500)
            h.rows[h.p2].append({"t": 3000, "known_at_ms": 3900, "bid": "2", "ask": "3"})
            h.p2.write_bytes(h.p2.read_bytes() + b"newer quote\n")
            with self.assertRaisesRegex(ContractError, "CROSS_SEGMENT_PRICE_CONFLICT"):
                cache.read_quotes(h.config, 4000, earliest_ms=1500)
            self.assertIsNone(cache._history)
            self.assertEqual(cache.history_fallbacks, 1)


    def test_inserted_source_with_old_conflict_requires_complete_history(self):
        tmp, h, cache = self.new()
        with tmp, h.patched():
            cache.read_quotes(h.config, 3000, earliest_ms=1500)
            folder = h.feed / "segment-0015"
            folder.mkdir(mode=0o700)
            path = folder / "quotes.jsonl"
            path.write_bytes(b"new source with an old quote\n")
            path.chmod(0o600)
            original_reader = h.read_source
            def reader(source):
                if source != path:
                    return original_reader(source)
                return h.headers.copy(), [{"t": 1000, "known_at_ms": 2900, "bid": "9", "ask": "10"}], {
                    "path": str(path), "bytes": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "uncommitted_tail_bytes": 0, "ended": False}
            def index(config, paths):
                self.assertEqual(paths, [h.p1, path, h.p2])
                return [dict(h.pin)], list(h.pinned_pages)
            with patch.object(module, "inventory_index", index), patch.object(module, "read_source", reader):
                with self.assertRaisesRegex(ContractError, "CROSS_SEGMENT_PRICE_CONFLICT"):
                    cache.read_quotes(h.config, 3000, earliest_ms=1500)
            self.assertIsNone(cache._history)
            self.assertEqual(cache.history_fallbacks, 1)



if __name__=="__main__":unittest.main()
