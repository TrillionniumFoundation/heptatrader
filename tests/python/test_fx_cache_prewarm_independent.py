"""Independent exact-byte 12-slot cold-prewarm receipt and status verification."""
from __future__ import annotations
from collections import Counter
import copy,hashlib,json,tempfile
from pathlib import Path
from unittest.mock import patch
import unittest
from train_v2 import fx_cache_prewarm_independent as audit

PERIOD="61995fcedc2993d7ac33d2ed86c9613068c5a3ecd0d1c377e612639cd5a0958f"
FIRST=1791910860000
def save(root,name,body):
    p=Path(root)/name
    data=audit.canonical(body)+b"\n"
    p.write_bytes(data)
    p.chmod(0o600)
    return audit.digest(data)
def status(at,hold=False):
    src=[]
    return {
        "schema":"heptatrader.quote-readiness-status.v1",
        "period_sha256":PERIOD,
        "rule":copy.deepcopy(audit.RULE),
        "probes":[{"status":"USABLE"} for _ in range(24)],
        "counts":{"USABLE":24},"usable":24,"unusable":0,
        "source_prefixes":src,"source_prefix_digest":audit.digest(audit.canonical(src)),
        "at_ms":at,"latest_source_quote_ms":at-(5122 if hold else 200),
        "latest_quote_age_ms":5122 if hold else 200,
        "state":"HOLD_NEW_FUSION_WINDOW" if hold else "READY_FOR_REVIEW_NOT_AUTHORIZATION",
        "reasons":["LATEST_AUTHORITATIVE_QUOTE_NOT_FRESH"] if hold else [],
        "trading_authority":False,"automatic_trial_registration":False,"orders_sent":0
    }
def fixture(root):
    root=Path(root);root.chmod(0o700)
    p={
       "schema":audit.STUDY+".plan.v1",
       "research_code_sha256":audit.CODE_PIN,
       "source_readiness_rule":copy.deepcopy(audit.RULE),
       "count":12,"spacing_ms":15000,
       "cycle_schedule_ms":[FIRST+15000*i for i in range(12)],
       "maximum_prepublication_original_age_ms":30000,
       "maximum_decision_start_lateness_ms":3000,
       "prewarm_must_finish_before_first_ms":FIRST-10000,
       "production_owner_witness_admission":False,
       "cutover_production_monitor":False,
       "trade_authority":False,
       "original_monitor_sha256":"63bcef1a6d8bc22e3c2fb10d1dcebc1457559538e6a959004e61eeb88c09c320",
       "source_cache_must_verify_all_sealed_bytes_every_cycle":True,
       "first_original_cache480_missed_cycle_is_preserved":True,
       "original_period_sha256":PERIOD,
    }
    ph=save(root,"plan.json",p)
    warm_start=FIRST-50000
    ws=status(warm_start)
    wsh=save(root,"prewarm-source.json",ws)
    w={
       "schema":audit.STUDY+".prewarm.v1",
       "source_bytes_sha256":wsh,
       "prewarm_within_frozen_before_first_deadline":True,
       "started_ms":warm_start,
       "finished_ms":FIRST-40000,
       "broker_orders_sent":0,"trade_authority":False,
       "source_health_issues":[],
       "original_source_state":"READY_FOR_REVIEW_NOT_AUTHORIZATION",
    }
    wh=save(root,"prewarm-receipt.json",w)
    chain="0"*64
    retained=[]
    for i in range(12):
        now=FIRST+15000*i
        is_hold=i==10
        h=save(root,f"slot-{i:02d}-original-status.json",status(now,is_hold))
        r={
            "schema":audit.STUDY+".slot.v1",
            "index":i,"target_ms":now,
            "assessment_started_ms":now,
            "published_ms":now+2500,
            "record_sealed_at_ms":now+2501,
            "status_age_at_publication_ms":2500,
            "source_original_asof_ms":now,
            "source_status_sha256":h,
            "prewarm_receipt_sha256":wh,
            "previous_receipt_sha256":chain,
            "state":"SOURCE_HOLD_RETAINED" if is_hold else "SOURCE_READY_RESEARCH_ONLY",
            "source_ready":not is_hold,
            "issues":[],"broker_orders_sent":0,"trade_authority":False,
        }
        chain=save(root,f"slot-{i:02d}-receipt.json",r)
        retained.append({"index":i,"state":r["state"],"source_ready":not is_hold,
                         "receipt_sha256":chain})
    final={
        "schema":audit.STUDY+".final.v1",
        "frozen_plan_sha256":ph,
        "prewarm_receipt_sha256":wh,
        "count":12,"slot_results":retained,
        "valid_original_status_count":12,"operational_ready_count":11,
        "max_original_asof_to_own_publication_ms":2500,
        "max_original_asof_to_next_actual_publication_ms":17500,
        "production_monitor_cutover":False,"trade_authority":False,
        "broker_queries_or_orders_sent":0,
    }
    save(root,"final.json",final)
    return ph

class TestOriginalPrewarmIndependent(unittest.TestCase):
    def test_full_exact_twelve_chain(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);ph=fixture(root)
            with patch.object(audit,"PLAN_PIN",ph):
                r=audit.audit(root,root/"a.json")
            self.assertEqual(r["slot_count"],12)
            self.assertEqual(r["source_ready_count"],11)
            self.assertEqual(r["genuine_source_hold_or_unqualified_count"],1)
            self.assertEqual(r["max_previous_asof_to_next_publication_ms"],17500)
            self.assertFalse(r["production_health_monitor_cutover_authorized"])
    def test_original_quote_freshness_hold_is_not_fabricated_ready(self):
        x=status(FIRST,hold=True)
        self.assertFalse(audit.check_status(x,PERIOD,FIRST,FIRST+2500)["original_ready"])
    def test_stale_quote_claimed_ready_is_refused(self):
        x=status(FIRST)
        x["latest_quote_age_ms"]=6000;x["latest_source_quote_ms"]=FIRST-6000
        with self.assertRaisesRegex(audit.AuditHold,"FALSE_ORIGINAL_READY"):
            audit.check_status(x,PERIOD,FIRST,FIRST+2500)
    def test_miscount_probes_refused(self):
        x=status(FIRST);x["counts"]["USABLE"]=23
        with self.assertRaisesRegex(audit.AuditHold,"ORIGINAL_PROBE_COUNT_FALSIFIED"):
            audit.check_status(x,PERIOD,FIRST,FIRST+2500)
    def test_tampered_original_source_metadata_refused(self):
        x=status(FIRST);x["source_prefix_digest"]="d"*64
        with self.assertRaisesRegex(audit.AuditHold,"ORIGINAL_PREFIX_HASH_MISMATCH"):
            audit.check_status(x,PERIOD,FIRST,FIRST+2500)
    def test_cannot_use_wrong_period(self):
        x=status(FIRST);x["period_sha256"]="e"*64
        with self.assertRaisesRegex(audit.AuditHold,"ORIGINAL_SOURCE_POLICY_CHANGED"):
            audit.check_status(x,PERIOD,FIRST,FIRST+2500)
    def test_cannot_use_future_original_timestamp(self):
        x=status(FIRST+5000)
        with self.assertRaisesRegex(audit.AuditHold,"ORIGINAL_ASOF_TIME_INVALID"):
            audit.check_status(x,PERIOD,FIRST,FIRST+6000)
    def test_duplicate_json_keys_rejected(self):
        with self.assertRaisesRegex(audit.AuditHold,"DUPLICATE_JSON_KEY"):
            audit.parse(b'{"ready":true,"ready":false}')
    def test_nonfinite_json_rejected(self):
        with self.assertRaisesRegex(audit.AuditHold,"NONFINITE_JSON"):
            audit.parse(b'{"x":NaN}')
    def test_public_input_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"x";p.write_text("{}");p.chmod(0o644)
            with self.assertRaisesRegex(audit.AuditHold,"PRIVATE_REGULAR_SOURCE_REQUIRED"):
                audit.private(p)
    def test_missing_intermediate_original_status_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);ph=fixture(root)
            (root/"slot-03-original-status.json").unlink()
            with patch.object(audit,"PLAN_PIN",ph):
                with self.assertRaises(FileNotFoundError):
                    audit.audit(root,root/"b.json")
    def test_edited_final_report_cannot_overrule_actual_retained_hold(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);ph=fixture(root)
            p=root/"final.json"
            doc=json.loads(p.read_bytes());doc["operational_ready_count"]=12
            save(root,"temp.json",doc)
            p.write_bytes((root/"temp.json").read_bytes())
            with patch.object(audit,"PLAN_PIN",ph):
                with self.assertRaisesRegex(audit.AuditHold,"ORIGINAL_FINAL_DOES_NOT_REPRODUCE"):
                    audit.audit(root,root/"c.json")
    def test_corrupt_original_receipt_chain_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);ph=fixture(root)
            p=root/"slot-07-receipt.json"
            rec=json.loads(p.read_bytes());rec["previous_receipt_sha256"]="0"*64
            p.write_bytes(audit.canonical(rec)+b"\n")
            with patch.object(audit,"PLAN_PIN",ph):
                with self.assertRaisesRegex(audit.AuditHold,"ORIGINAL_CYCLE_CHAIN_OR_IDENTITY_CHANGED"):
                    audit.audit(root,root/"d.json")
    def test_cannot_overwrite_audit_result(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);ph=fixture(root)
            output=root/"e.json"
            with patch.object(audit,"PLAN_PIN",ph):
                audit.audit(root,output)
                with self.assertRaisesRegex(audit.AuditHold,"PRIVATE_OUTPUT_MUST_BE_NEW"):
                    audit.audit(root,output)

if __name__=="__main__":unittest.main()
