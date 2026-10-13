"""Adversarial separation of read-only monitor publication lag and IB quote TTL."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest

from train_v2.fx_source_monitor_timing_audit import (
    AuditHold, CYCLE_SCHEMA, READY, HOLD, RULE, EXPECTED_PAPER_PERIOD,
    canonical, sha, strict_json, private_file, expected_cycle_events,
    validate_cycle_stream, validate_last_status,
)

T0=1791865900000

def stream(*,cycles=24,interval_ms=15000,publish_age_ms=4800):
    out=[]
    for j in range(cycles):
        at=T0+j*interval_ms
        out.append({
            "schema":CYCLE_SCHEMA,"cycle":j+1,
            "source_asof_ms":at,"published_ms":at+publish_age_ms,
            "published_source_age_ms":publish_age_ms,
            "assess_ms":publish_age_ms-2,
            "state":READY,"usable":24,
            "trade_authority":False,"orders_sent":0,
        })
    return out

def status(last):
    proofs=[{"original_sha":"a"*64,"last_bytes":345,"path":"/private/quotes.jsonl"}]
    probes=[{"status":"USABLE","start_ms":T0-i*60000} for i in range(24)]
    return {
        "schema":"heptatrader.quote-readiness-status.v1",
        "period_sha256":EXPECTED_PAPER_PERIOD,
        "rule":copy.deepcopy(RULE),
        "at_ms":last["source_asof_ms"],
        "state":last["state"],
        "usable":last["usable"],"unusable":0,
        "counts":{"USABLE":24},"probes":probes,
        "source_prefixes":proofs,
        "source_prefix_digest":sha(canonical(proofs)),
        "latest_source_quote_ms":last["source_asof_ms"]-1333,
        "latest_quote_age_ms":1333,"reasons":[],
        "automatic_trial_registration":False,
        "trading_authority":False,"orders_sent":0,
    }

class ProtocolTests(unittest.TestCase):
    def test_complete_15s_publisher_separates_market_readiness(self):
        r=validate_cycle_stream(stream())
        self.assertEqual(r["cycle_count"],24)
        self.assertEqual(r["source_ready_cycle_count"],24)
        self.assertEqual(r["source_hold_cycle_count"],0)
        self.assertEqual(r["monitor_snapshot_age_before_next_publish_max_ms"],19800)
        self.assertTrue(r["all_observed_monitor_intervals_within_30s"])
    def test_24_usable_but_IB_quote_stale_is_still_HOLD(self):
        events=stream()
        events[3]["state"]=HOLD
        r=validate_cycle_stream(events)
        self.assertEqual(r["source_hold_cycle_count"],1)
        self.assertEqual(r["source_ready_cycle_count"],23)
        self.assertTrue(r["all_observed_monitor_intervals_within_30s"])
        self.assertTrue(r["quote_health_HOLD_cycles_still_HOLD"])
    def test_slow_quota_creates_between_publication_sla_gap(self):
        r=validate_cycle_stream(stream(interval_ms=17000,publish_age_ms=16993))
        self.assertFalse(r["all_observed_monitor_intervals_within_30s"])
        self.assertGreater(r["monitor_snapshot_age_before_next_publish_max_ms"],30000)
        self.assertGreater(r["monitor_intervals_exceeding_declared_30s_age"],0)
    def test_published_stale_independently_fails(self):
        r=validate_cycle_stream(stream(publish_age_ms=33000))
        self.assertFalse(r["all_observed_monitor_intervals_within_30s"])
        self.assertEqual(r["monitor_published_samples_exceeding_declared_30s_age"],24)
    def test_missing_cycle_denominator_refused(self):
        with self.assertRaisesRegex(AuditHold,"SOURCE_CYCLE_MISSING"):
            validate_cycle_stream(stream(cycles=23))
    def test_duplicated_cycle_id_refused(self):
        e=stream();e[15]["cycle"]=15
        with self.assertRaisesRegex(AuditHold,"UNTRUSTED_SOURCE_CYCLE_RECORD"):
            validate_cycle_stream(e)
    def test_regressing_asof_refused(self):
        e=stream();e[17]["source_asof_ms"]=e[16]["source_asof_ms"]-5
        e[17]["published_source_age_ms"]=e[17]["published_ms"]-e[17]["source_asof_ms"]
        with self.assertRaisesRegex(AuditHold,"MONITOR_CYCLE_CLOCK_NONMONOTONIC"):
            validate_cycle_stream(e)
    def test_fake_published_age_refused(self):
        e=stream();e[7]["published_source_age_ms"]+=1
        with self.assertRaisesRegex(AuditHold,"SOURCE_CYCLE_CLOCK_OR_ASOF_FORGED"):
            validate_cycle_stream(e)
    def test_ready_but_insufficient_usable_refused(self):
        e=stream();e[3]["usable"]=19
        with self.assertRaisesRegex(AuditHold,"PUBLISHED_READY_WITH_INVALID_PROBES"):
            validate_cycle_stream(e)
    def test_unknown_market_state_refused(self):
        e=stream();e[0]["state"]="TRADE_ALLOWED"
        with self.assertRaisesRegex(AuditHold,"UNTRUSTED_SOURCE_CYCLE_RECORD"):
            validate_cycle_stream(e)
    def test_market_quote_hold_with_missing_usable_is_observed_not_recovered(self):
        e=stream();e[0]["state"]=HOLD;e[0]["usable"]=None
        r=validate_cycle_stream(e)
        self.assertEqual(r["source_hold_cycle_count"],1)
        self.assertTrue(r["quote_health_reason_per_cycle_not_present_in_summary"])
    def test_late_authority_flip_refused(self):
        e=stream();e[-1]["trade_authority"]=True
        with self.assertRaisesRegex(AuditHold,"UNTRUSTED_SOURCE_CYCLE_RECORD"):
            validate_cycle_stream(e)
    def test_final_source_and_24_probes_exact(self):
        e=stream()
        self.assertTrue(validate_last_status(status(e[-1]),e[-1],EXPECTED_PAPER_PERIOD))
    def test_final_status_not_bound_to_last_cycle_rejected(self):
        e=stream();d=status(e[-1]);d["at_ms"]-=1000
        with self.assertRaisesRegex(AuditHold,"FINAL_STATUS_NOT_LAST_JOURNAL_SOURCE"):
            validate_last_status(d,e[-1],EXPECTED_PAPER_PERIOD)
    def test_24_probe_mismatch_refused(self):
        e=stream();d=status(e[-1]);d["probes"][0]["status"]="PATH_GAP"
        with self.assertRaisesRegex(AuditHold,"LAST_STATUS_24_PROBES_COUNT_MISMATCH"):
            validate_last_status(d,e[-1],EXPECTED_PAPER_PERIOD)
    def test_source_prefix_digest_tampering_refused(self):
        e=stream();d=status(e[-1]);d["source_prefixes"][0]["last_bytes"]=999
        with self.assertRaisesRegex(AuditHold,"FINAL_SOURCE_PREFIX_DIGEST_CHANGED"):
            validate_last_status(d,e[-1],EXPECTED_PAPER_PERIOD)
    def test_broker_quote_ttl_exceeding_5s_cannot_be_READY(self):
        e=stream();d=status(e[-1]);d["latest_quote_age_ms"]=7000
        d["latest_source_quote_ms"]=d["at_ms"]-7000
        with self.assertRaisesRegex(AuditHold,"READY_EXCEEDED_ORIGINAL_QUOTE_TTL"):
            validate_last_status(d,e[-1],EXPECTED_PAPER_PERIOD)
    def test_period_scope_refused(self):
        e=stream();d=status(e[-1]);d["period_sha256"]="f"*64
        with self.assertRaisesRegex(AuditHold,"FINAL_ORIGINAL_MONITOR_SCOPE_CHANGED"):
            validate_last_status(d,e[-1],EXPECTED_PAPER_PERIOD)
    def test_rule_weaken_refused(self):
        e=stream();d=status(e[-1]);d["rule"]["source_ttl_ms"]=10000
        with self.assertRaisesRegex(AuditHold,"FINAL_ORIGINAL_MONITOR_SCOPE_CHANGED"):
            validate_last_status(d,e[-1],EXPECTED_PAPER_PERIOD)
    def test_dup_json_keys_rejected(self):
        with self.assertRaisesRegex(AuditHold,"DUPLICATE_JSON_KEY"):
            strict_json(b'{"state":"READY","state":"TRADE"}')
    def test_nonfinite_json_refused(self):
        with self.assertRaisesRegex(AuditHold,"NONFINITE_JSON"):
            strict_json(b'{"value":NaN}')
    def test_real_journal_ignores_unrelated_lines(self):
        events=stream(cycles=2)
        text=b"systemd started\n"+canonical(events[0])+b"\nsomething ignored\n"+canonical(events[1])+b"\n"
        self.assertEqual(expected_cycle_events(text),events)
    def test_public_input_file_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/"snap.json";p.write_text('{"ok":true}');p.chmod(0o644)
            with self.assertRaisesRegex(AuditHold,"INPUT_NOT_OWNER_PRIVATE_REGULAR_FILE"):
                private_file(p)

if __name__=="__main__":
    unittest.main()
