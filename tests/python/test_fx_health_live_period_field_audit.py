"""Hostile sample/reconciliation tests for period-bound monitor freshness."""
import copy,hashlib,json,os,pathlib,tempfile,unittest
from train_v2.fx_health_live_period_field_audit import (
    validate,FieldHold,parse,sha,read_private,SCHEMA,
)
PERIOD="a"*64
def fixture():
    base=1791830732355
    entries=[]
    for i in range(360):
        at=base+i*250
        age=10000+(i%50)*1500
        entries.append({
            "at_ms":at,"period_sha256":PERIOD,
            "producer_at_ms":at-age,"age_ms":age,
            "producer_state":"READY_FOR_REVIEW_NOT_AUTHORIZATION",
            "producer_usable":24,
            "producer_sha256":("b" if i<180 else "c")*64,
        })
    return entries
def encode(entries):
    return b"".join(json.dumps(r,sort_keys=True,separators=(",",":")).encode()+b"\n"
                    for r in entries)
def summary(rows):
    b=encode(rows);ages=[r.get("age_ms") for r in rows if "age_ms" in r]
    states=[r.get("producer_state") for r in rows if "age_ms" in r]
    ds=[r.get("producer_sha256") for r in rows if "age_ms" in r]
    return {"schema":SCHEMA,"original_paper_period_sha256":PERIOD,
            "sample_count":len(rows),"sampling_interval_target_ms":250,
            "trading_or_original_trial_modified":False,
            "broker_requests_or_orders":0,"sample_log_sha256":sha(b),
            "unique_producer_status_digests":len(set(ds)),
            "age_min_ms":min(ages),"age_max_ms":max(ages),
            "counts":{"readable":len(ages),
               "missing":sum(x.get("error")=="ENOENT" for x in rows),
               "incorrect_or_invalid":sum(x.get("error")=="WRONG_OR_UNDECODABLE" for x in rows),
               "age_le_30s":sum(0<=x<=30000 for x in ages),
               "age_over_30s":sum(x>30000 or x<0 for x in ages),
               "ready":sum(x=="READY_FOR_REVIEW_NOT_AUTHORIZATION" for x in states),
               "hold":sum(x=="HOLD_NEW_FUSION_WINDOW" for x in states)}}
class FieldAuditTests(unittest.TestCase):
    def test_fully_reproduced_truth_rejects_stale_ready_as_authority(self):
        rows=fixture(); r=validate(encode(rows),summary(rows),PERIOD)
        self.assertEqual(r["readable"],360)
        self.assertEqual(r["file_missing"],0)
        self.assertGreater(r["older_than_30s"],0)
        self.assertFalse(r["snapshot_30s_requirement_met_at_all_sampled_times"])
        self.assertFalse(r["original_quote_source_or_broker_signature_verified"])
    def test_same_when_every_ready_within_budget(self):
        rows=fixture()
        for a in rows:
            a["age_ms"]=10000
            a["producer_at_ms"]=a["at_ms"]-10000
        r=validate(encode(rows),summary(rows),PERIOD)
        self.assertEqual(r["older_than_30s"],0)
        self.assertTrue(r["snapshot_30s_requirement_met_at_all_sampled_times"])
    def test_report_digest_tamper_rejected(self):
        rows=fixture();s=summary(rows);s["sample_log_sha256"]="0"*64
        with self.assertRaisesRegex(FieldHold,"SOURCE_SAMPLE_BYTES_CHANGED"):
            validate(encode(rows),s,PERIOD)
    def test_truncated_population_rejected(self):
        rows=fixture()[:-1]
        evidence=summary(rows)
        evidence["sample_count"]=360
        with self.assertRaisesRegex(FieldHold,"FROZEN_360_SAMPLES_REQUIRED"):
            validate(encode(rows),evidence,PERIOD)
    def test_wrong_period_rejected(self):
        rows=fixture()
        with self.assertRaisesRegex(FieldHold,"FIELD_PROVENANCE_OR_RELEASE_INVALID"):
            validate(encode(rows),summary(rows),"e"*64)
    def test_sample_claim_wrong_period_rejected(self):
        rows=fixture();rows[33]["period_sha256"]="e"*64
        with self.assertRaisesRegex(FieldHold,"SAMPLE_SCOPE_OR_CLOCK_INVALID"):
            validate(encode(rows),summary(rows),PERIOD)
    def test_fake_age_refused_even_when_summary_is_recomputed(self):
        rows=fixture();rows[7]["age_ms"]+=1000
        with self.assertRaisesRegex(FieldHold,"MONITOR_ASOF_OR_CLOCK_DISCREPANCY"):
            validate(encode(rows),summary(rows),PERIOD)
    def test_future_asof_refused(self):
        rows=fixture();rows[6]["producer_at_ms"]=rows[6]["at_ms"]+2
        rows[6]["age_ms"]=-2
        with self.assertRaisesRegex(FieldHold,"MONITOR_ASOF_OR_CLOCK_DISCREPANCY"):
            validate(encode(rows),summary(rows),PERIOD)
    def test_out_of_order_sample_refused(self):
        rows=fixture();rows[3]["at_ms"]=rows[2]["at_ms"]-100
        rows[3]["producer_at_ms"]=rows[3]["at_ms"]-rows[3]["age_ms"]
        with self.assertRaisesRegex(FieldHold,"SAMPLE_POLL_INTERVAL_CHANGED"):
            validate(encode(rows),summary(rows),PERIOD)
    def test_large_gap_refused(self):
        rows=fixture()
        for row in rows[200:]:
            row["at_ms"]+=3000
            row["producer_at_ms"]+=3000
        with self.assertRaisesRegex(FieldHold,"SAMPLE_POLL_INTERVAL_CHANGED"):
            validate(encode(rows),summary(rows),PERIOD)
    def test_unauthorized_extra_field_rejected(self):
        rows=fixture();rows[123]["model_promoted"]=True
        with self.assertRaisesRegex(FieldHold,"MONITOR_SAMPLE_FIELDS_CHANGED"):
            validate(encode(rows),summary(rows),PERIOD)
    def test_summary_counts_tamper_rejected(self):
        rows=fixture();s=summary(rows);s["counts"]["age_over_30s"]-=1
        with self.assertRaisesRegex(FieldHold,"PUBLISHED_SOURCE_FRESHNESS_COUNTERS_INCORRECT"):
            validate(encode(rows),s,PERIOD)
    def test_status_hold_but_not_missing(self):
        rows=fixture();rows[20]["producer_state"]="HOLD_NEW_FUSION_WINDOW"
        out=validate(encode(rows),summary(rows),PERIOD)
        self.assertEqual(out["hold"],1)
        self.assertEqual(out["file_missing"],0)
    def test_missing_file_counted_not_synthetic_valid_status(self):
        rows=fixture();r=rows[80]
        rows[80]={"at_ms":r["at_ms"],"period_sha256":PERIOD,"error":"ENOENT"}
        out=validate(encode(rows),summary(rows),PERIOD)
        self.assertEqual(out["file_missing"],1)
        self.assertFalse(out["snapshot_30s_requirement_met_at_all_sampled_times"])
    def test_error_row_with_fake_reward_refused(self):
        rows=fixture();r=rows[79]
        rows[79]={"at_ms":r["at_ms"],"period_sha256":PERIOD,"error":"ENOENT","trade_profit":123}
        with self.assertRaisesRegex(FieldHold,"SAMPLE_ERROR_NOT_HONEST"):
            validate(encode(rows),summary(rows),PERIOD)
    def test_invalid_original_period_length(self):
        rows=fixture()
        with self.assertRaisesRegex(FieldHold,"PAPER_PERIOD_NOT_SHA256"):
            validate(encode(rows),summary(rows),"a"*42)
    def test_reject_duplicate_keys_and_nan(self):
        with self.assertRaisesRegex(FieldHold,"DUPLICATE_JSON_KEY"):
            parse(b'{"a":1,"a":2}')
        with self.assertRaisesRegex(FieldHold,"NONFINITE_JSON"):
            parse(b'{"a":NaN}')
    def test_unsafe_public_input(self):
        with tempfile.TemporaryDirectory() as t:
            p=pathlib.Path(t)/"sample.json"
            p.write_text("{}");p.chmod(0o644)
            with self.assertRaisesRegex(FieldHold,"ORIGINAL_RESEARCH_FILE_NOT_PRIVATE"):
                read_private(p)
if __name__=="__main__":unittest.main()
