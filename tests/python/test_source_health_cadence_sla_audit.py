"""Adversarial tests for precise as-of timestamp age between monitor publishes."""
import copy, json, tempfile, unittest
from pathlib import Path
from train_v2.source_health_cadence_sla_audit import (
    SCHEMA,EVENT_SCHEMA,AuditHold,audit_cycles,canonical,extract_from_journal,
    normalized_record,write_private
)
def samples(pub_latency=16000,period=17000,n=4):
    first=1791827256166
    records=[]
    for i in range(n):
        a=first+i*period
        records.append({
            "schema":EVENT_SCHEMA,"cycle":i+1,
            "assess_ms":pub_latency,
            "source_asof_ms":a,"published_ms":a+pub_latency,
            "published_source_age_ms":pub_latency,
            "state":"READY_FOR_REVIEW_NOT_AUTHORIZATION",
            "usable":24,"trade_authority":False,"orders_sent":0,
        })
    return records

class CadenceSlaAuditTests(unittest.TestCase):
    def test_cpu25_nominal_fast_label_not_sufficient(self):
        a=samples(pub_latency=16000,period=17000,n=8)
        r=audit_cycles(a,quota_percent=25,expected_cycles=8,
                       requested_period_seconds=15)
        self.assertFalse(r["all_observed_intervals_continuously_fresh"])
        self.assertEqual(r["observed_stale_intervals"],7)
        self.assertEqual(r["max_stale_age_immediately_before_next_publish_ms"],33000)
        self.assertEqual(r["observed_stale_exposure_total_ms"],21000)
    def test_cpu50_can_be_fresh_in_small_bounded_trace(self):
        a=samples(pub_latency=8200,period=15000,n=12)
        r=audit_cycles(a,quota_percent=50,expected_cycles=12,
                       requested_period_seconds=15)
        self.assertTrue(r["all_observed_intervals_continuously_fresh"])
        self.assertEqual(r["observed_stale_intervals"],0)
        self.assertLess(r["max_stale_age_immediately_before_next_publish_ms"],30000)
        self.assertTrue(r["short_research_duration_cannot_certify_production_durability"])
        self.assertFalse(r["trade_authority"])
    def test_any_single_bad_interval_keeps_overall_hold(self):
        a=samples(pub_latency=8200,period=15000,n=8)
        for i in range(4,8):
            a[i]["source_asof_ms"]+=10000
            a[i]["published_ms"]+=10000
        r=audit_cycles(a,quota_percent=25,expected_cycles=8,
                       requested_period_seconds=15)
        self.assertGreater(r["observed_stale_intervals"],0)
    def test_missing_cycle_fails_closed(self):
        a=samples(n=8);a.pop(3)
        with self.assertRaisesRegex(AuditHold,"INCOMPLETE_OR_REPEATED"):
            audit_cycles(a,quota_percent=25,expected_cycles=8,requested_period_seconds=15)
    def test_duplicate_cycle_fails_closed(self):
        a=samples(n=8);a[3]["cycle"]=3
        with self.assertRaisesRegex(AuditHold,"MISSING_OR_DUPLICATE"):
            audit_cycles(a,quota_percent=25,expected_cycles=8,requested_period_seconds=15)
    def test_bad_source_timestamp_rejected(self):
        a=samples(n=8);a[3]["source_asof_ms"]=a[2]["source_asof_ms"]
        a[3]["published_source_age_ms"]=a[3]["published_ms"]-a[3]["source_asof_ms"]
        a[3]["assess_ms"]=a[3]["published_source_age_ms"]
        with self.assertRaisesRegex(AuditHold,"NOT_MONOTONIC"):
            audit_cycles(a,quota_percent=25,expected_cycles=8,requested_period_seconds=15)
    def test_relabel_age_rejected(self):
        a=samples(n=8);a[3]["published_source_age_ms"]-=1000
        with self.assertRaisesRegex(AuditHold,"ASOF_AGE_NOT_REPRODUCIBLE"):
            audit_cycles(a,quota_percent=25,expected_cycles=8,requested_period_seconds=15)
    def test_trading_authority_rejected(self):
        a=samples(n=8);a[3]["trade_authority"]=True
        with self.assertRaisesRegex(AuditHold,"NOT_A_RESEARCH_SOURCE"):
            audit_cycles(a,quota_percent=25,expected_cycles=8,requested_period_seconds=15)
    def test_broker_order_claim_rejected(self):
        a=samples(n=8);a[3]["orders_sent"]=1
        with self.assertRaisesRegex(AuditHold,"NOT_A_RESEARCH_SOURCE"):
            audit_cycles(a,quota_percent=25,expected_cycles=8,requested_period_seconds=15)
    def test_numeric_bool_rejected(self):
        a=samples(n=8);a[2]["cycle"]=True
        with self.assertRaisesRegex(AuditHold,"NUMERIC_TYPE"):
            audit_cycles(a,quota_percent=25,expected_cycles=8,requested_period_seconds=15)
    def test_extra_fields_rejected(self):
        a=samples(n=8);a[3]["broker_token"]="secret"
        with self.assertRaisesRegex(AuditHold,"FIELDS_UNEXPECTED"):
            audit_cycles(a,quota_percent=25,expected_cycles=8,requested_period_seconds=15)
    def test_wrong_quota_rejected(self):
        with self.assertRaisesRegex(AuditHold,"CPU_BUDGET_NOT_TESTED"):
            audit_cycles(samples(n=8),quota_percent=1,expected_cycles=8,
                         requested_period_seconds=15)
    def test_wrong_cadence_rejected(self):
        with self.assertRaisesRegex(AuditHold,"RELEASE_CADENCE_CHANGED"):
            audit_cycles(samples(n=8),quota_percent=25,expected_cycles=8,
                         requested_period_seconds=60)
    def test_journal_extract_ignores_only_irrelevant_lines(self):
        a=samples(n=3)
        raw=["service started",canonical(a[0]).decode(),
             canonical(a[1]).decode(),canonical(a[2]).decode(),
             "service stopped"]
        self.assertEqual(extract_from_journal(raw),a)
    def test_corrupt_candidate_journal_cycle_rejected(self):
        a=samples(n=3)
        broken=canonical(a[1]).decode()[:-1]
        with self.assertRaisesRegex(AuditHold,"CORRUPT_EXPECTED_JOURNAL_EVENT"):
            extract_from_journal([canonical(a[0]).decode(),broken])
    def test_duplicate_event_json_key_rejected(self):
        a=samples(n=3)
        raw=canonical(a[0]).decode().replace('"cycle":1','"cycle":1,"cycle":1')
        with self.assertRaisesRegex(AuditHold,"CORRUPT_EXPECTED_JOURNAL_EVENT"):
            extract_from_journal([raw])
    def test_receipt_write_once(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/"evidence";p.mkdir(mode=0o700)
            f=p/"receipt.json"
            write_private(f,b'{"state":"HOLD"}\n')
            self.assertEqual(f.read_bytes(),b'{"state":"HOLD"}\n')
            self.assertEqual(f.stat().st_mode & 0o777,0o600)
            with self.assertRaisesRegex(AuditHold,"OUTPUT_MUST_BE_NEW_ABSOLUTE"):
                write_private(f,b'{"state":"PASS"}\n')
    def test_public_output_dir_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/"evidence";p.mkdir(mode=0o755)
            with self.assertRaisesRegex(AuditHold,"OUTPUT_DIRECTORY_NOT_PRIVATE"):
                write_private(p/"receipt.json",b'{}\n')
if __name__=="__main__":unittest.main()
