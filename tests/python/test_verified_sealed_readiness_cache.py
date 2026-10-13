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

if __name__=="__main__":unittest.main()
