"""Independent 25%-CPU cached-source freshness SLA and journal custody tests."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from train_v2.source_health_cache_sla_review import (
    SCHEMA,UNIT,AuditHold,identify_terminal,review,run
)

def fixtures():
    start=1_791_829_176_513
    events=[]
    for i in range(24):
        if i==0: a=start; age=16000
        else: a=start+17000+15000*(i-1); age=1000
        e={"schema":"heptatrader.source-readiness-cadence-lab.v1",
           "cycle":i+1,"assess_ms":age,"source_asof_ms":a,
           "published_ms":a+age,"published_source_age_ms":age,
           "state":"READY_FOR_REVIEW_NOT_AUTHORIZATION","usable":24,
           "trade_authority":False,"orders_sent":0}
        events.append(e)
    cache={"schema":SCHEMA,
       "archive_inventories_full_sha256_reverified":24,
       "sealed_decoding_reused":256*23,
       "sealed_hits_bytes_reverified_at_use":256*23,
       "sealed_decoding_strictly_parsed":256,
       "unsealed_or_extra_strictly_reparsed":5*24,
       "cached_sealed_segments":256,
       "cached_original_quote_records":116289,
       "cache_is_not_independent_broker_signature":True,
       "trade_authority":False,"broker_orders_sent":0}
    lines=[json.dumps(x,sort_keys=True).encode() for x in events]+[
        json.dumps(cache,sort_keys=True).encode()]
    return events,cache,lines

class ReplayTests(unittest.TestCase):
    def test_actual_contract_good_synthetic_replay(self):
        _,_,lines=fixtures()
        r,events,c=review(lines)
        self.assertEqual(r["observed_cycle_count"],24)
        self.assertEqual(len(events),24)
        self.assertTrue(r["all_observed_intervals_continuously_fresh"])
        self.assertTrue(r["all_24_cycle_health_states_are_ready"])
        self.assertEqual(r["first_cold_assessment_ms"],16000)
        self.assertEqual(r["warm_assessment_max_ms"],1000)
        self.assertEqual(c["cached_sealed_segments"],256)
    def test_missing_terminal_counter_fails(self):
        *_,lines=fixtures()
        with self.assertRaisesRegex(AuditHold,"MISSING_OR_DUPLICATE_CACHED_COUNTER_JOURNAL"):
            review(lines[:-1])
    def test_duplicate_terminal_counter_fails(self):
        *_,lines=fixtures()
        with self.assertRaisesRegex(AuditHold,"MISSING_OR_DUPLICATE_CACHED_COUNTER_JOURNAL"):
            review(lines+[lines[-1]])
    def test_altered_archived_pin_recheck_count_fails(self):
        _,cache,lines=fixtures()
        cache["archive_inventories_full_sha256_reverified"]=23
        with self.assertRaisesRegex(AuditHold,"NOT_EVERY_CYCLE_REVERIFIED"):
            review(lines[:-1]+[json.dumps(cache).encode()])
    def test_missing_sealed_cache_usage_fails(self):
        _,cache,lines=fixtures()
        cache["sealed_decoding_reused"]=0
        with self.assertRaisesRegex(AuditHold,"SEALED_CACHED_DECODER_NOT_ACTIVE"):
            review(lines[:-1]+[json.dumps(cache).encode()])
    def test_cached_hit_not_hashed_again_fails(self):
        _,cache,lines=fixtures()
        cache["sealed_hits_bytes_reverified_at_use"]=0
        with self.assertRaisesRegex(AuditHold,"CACHED_HIT_BYTES_NOT_REVERIFIED_BEFORE_USE"):
            review(lines[:-1]+[json.dumps(cache).encode()])

    def test_missing_unsealed_reparse_fails(self):
        _,cache,lines=fixtures()
        cache["unsealed_or_extra_strictly_reparsed"]=0
        with self.assertRaisesRegex(AuditHold,"UNSEALED_SOURCE_WAS_NOT_REPARSED"):
            review(lines[:-1]+[json.dumps(cache).encode()])
    def test_attempted_trading_signature_claim_fails(self):
        _,cache,lines=fixtures()
        cache["trade_authority"]=True
        with self.assertRaisesRegex(AuditHold,"CACHED_MONITOR_CLAIMS_UNAUTHORIZED"):
            review(lines[:-1]+[json.dumps(cache).encode()])
    def test_cached_entry_numeric_bool_refused(self):
        _,cache,lines=fixtures()
        cache["cached_sealed_segments"]=True
        with self.assertRaisesRegex(AuditHold,"CACHED_COUNTER_TYPE_OR_RANGE"):
            review(lines[:-1]+[json.dumps(cache).encode()])
    def test_wrong_counter_shape_refused(self):
        _,cache,lines=fixtures()
        cache["model_profit"]="1000 USD"
        with self.assertRaisesRegex(AuditHold,"CACHED_COUNTER_FIELDS_CHANGED"):
            review(lines[:-1]+[json.dumps(cache).encode()])
    def test_corrupted_terminal_json_fails(self):
        _,cache,lines=fixtures()
        corrupt=lines[-1][:-2]+b",}"
        with self.assertRaisesRegex(AuditHold,"CORRUPT_CACHED_COUNTER_JOURNAL"):
            review(lines[:-1]+[corrupt])
    def test_missing_cycle_fail(self):
        _,_,lines=fixtures()
        with self.assertRaisesRegex(AuditHold,"LIVE_LAB_INCOMPLETE_OR_REPEATED"):
            review(lines[:12]+lines[13:])
    def test_corrupt_asof_to_publish_is_detected(self):
        events,cache,_=fixtures()
        events[8]["published_source_age_ms"]=22
        lines=[json.dumps(x).encode() for x in events]+[json.dumps(cache).encode()]
        with self.assertRaisesRegex(AuditHold,"ASOF_AGE_NOT_REPRODUCIBLE"):
            review(lines)
    def test_stale_interval_not_misreported_as_pass(self):
        events,cache,_=fixtures()
        # Continuously slowed publication with still valid actual at/publish
        # clocks, so the 30s envelope must FAIL despite nominal 15s intent.
        for i in range(1,len(events)):
            events[i]["source_asof_ms"]+=i*22000
            events[i]["published_ms"]+=i*22000
        lines=[json.dumps(x).encode() for x in events]+[json.dumps(cache).encode()]
        r,_,_=review(lines)
        self.assertFalse(r["all_observed_intervals_continuously_fresh"])
        self.assertGreater(r["observed_stale_exposure_total_ms"],0)
    def test_raw_existing_output_no_overwrite(self):
        _,_,lines=fixtures()
        raw=b"\n".join(lines)+b"\n"
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/"private";root.mkdir(mode=0o700)
            p=root/"cached-original-24-cycles.jsonl"
            p.write_text("PRIOR_RECEIPT")
            p.chmod(0o600)
            with patch("train_v2.source_health_cache_sla_review.subprocess.check_output",
                       return_value=raw):
                with self.assertRaisesRegex(AuditHold,"OUTPUT_MUST_BE_NEW_ABSOLUTE"):
                    run(root)
            self.assertEqual(p.read_text(),"PRIOR_RECEIPT")
    def test_raw_replay_saved_0600_only(self):
        _,_,lines=fixtures()
        raw=b"\n".join(lines)+b"\n"
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/"private";root.mkdir(mode=0o700)
            with patch("train_v2.source_health_cache_sla_review.subprocess.check_output",
                       return_value=raw):
                run(root)
            self.assertEqual((root/"cached-sla-review.json").stat().st_mode & 0o777,0o600)
            self.assertEqual((root/"cached-terminal-counters.json").stat().st_mode & 0o777,0o600)

if __name__=="__main__":unittest.main()
