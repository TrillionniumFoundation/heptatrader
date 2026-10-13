"""Adversarial unit tests for original cached source timeline and duration SLA."""
import json
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from train_v2 import fx_cache480_sla_timeline as t

START = 1900000000000
PERIOD = t.PERIOD_PIN

def output(path,obj):
    raw=t.canonical(obj)+b"\n"
    path.write_bytes(raw)
    path.chmod(0o600)
    return t.sha(raw)

def fixture(root):
    root.mkdir(mode=0o700)
    schedule=[START+15000*i for i in range(480)]
    plan={
      "schema":t.COHORT+".plan.v1","period_sha256":PERIOD,
      "count":480,"spacing_ms":15000,"maximum_original_status_age_ms":30000,
      "exact_original_monitor_rule":t.RULE,
      "trade_authority":False,"broker_queries_or_orders_sent":0,
      "production_cutover_authorized":False,
      "cycle_schedule_ms":schedule
    }
    plan_sha=output(root/"plan.json",plan)
    worker={
      "plan_sha256":plan_sha,"no_restart_no_retry":True,
      "no_production_service_mutation":True,"broker_orders_sent":0}
    output(root/"worker-start.json",worker)
    return plan_sha

def append(root,i,prior, *, state="READY", pub_delay=1800,missing=False):
    at=START+i*15000
    n=24 if state=="READY" else 19
    statuses=["USABLE"]*n+["PATH_GAP"]*(24-n)
    source={
        "schema":"heptatrader.quote-readiness-status.v1",
        "period_sha256":PERIOD,"rule":t.RULE,
        "state":"READY_FOR_REVIEW_NOT_AUTHORIZATION"
            if state=="READY" else "HOLD_NEW_FUSION_WINDOW",
        "reasons":[] if state=="READY" else ["INSUFFICIENT_ENDPOINT_AND_PATH_COVERAGE"],
        "counts":{"USABLE":n} if n==24 else {"USABLE":n,"PATH_GAP":5},
        "usable":n,"unusable":24-n,
        "probes":[{"status":x} for x in statuses],
        "source_prefixes":[],"source_prefix_digest":t.sha(t.canonical([])),
        "at_ms":at,"latest_source_quote_ms":at-100,
        "latest_quote_age_ms":100,
        "automatic_trial_registration":False,"trading_authority":False,
        "orders_sent":0,
    }
    status_sha=None
    if not missing:
        status_sha=output(root/("cycle-%03d-status.json"%i),source)
    rec={
        "schema":t.COHORT+".cycle.v1",
        "index":i,"fixed_target_ms":at,
        "actual_assessment_started_ms":at,
        "record_sealed_at_ms":at+pub_delay,
        "previous_receipt_sha256":prior,
        "period_sha256":PERIOD,
        "trade_authority":False,"broker_orders_sent":0,
        "state":"HOLD_SOURCE_ASSESSMENT_FAILURE" if missing
               else "CAPTURED_ORIGINAL_CACHE_HEALTH",
        "issues":["SOURCE_FAILURE_Test"] if missing else [],
        "status_sha256":status_sha,
        "source_ready":state=="READY" and not missing,
    }
    if not missing:
        rec.update(original_asof_ms=at,
                   published_at_ms=at+pub_delay,
                   original_reported_state=source["state"],
                   source_usable_probes=n,
                   original_monitor_source_prefix_digest=source["source_prefix_digest"])
    return output(root/("cycle-%03d-receipt.json"%i),rec)

def sample(*,states=("READY","HOLD")):
    temp=tempfile.TemporaryDirectory()
    root=Path(temp.name)/"evidence"
    pinned=fixture(root)
    prev="0"*64
    for i,s in enumerate(states):
        prev=append(root,i,prev,state=s,missing=s=="MISSING")
    return temp,root,pinned

def scan_with_pin(root,pinned):
    with patch.object(t,"PLAN_PIN",pinned):
        return t.scan(root)

class SourceTimelineTests(unittest.TestCase):
    def test_genuine_ready_and_hold_disjoint(self):
        temp,root,pin=sample()
        with temp:
            r=scan_with_pin(root,pin)
            self.assertEqual(r["prefix_cycle_count"],2)
            self.assertEqual(r["cycle_classes"]["READY"],1)
            self.assertEqual(r["cycle_classes"]["ORIGINAL_HOLD"],1)
            self.assertEqual(r["original_probe_status_totals"]["USABLE"],43)
            self.assertFalse(r["complete_480_evidence_observed"])
            self.assertFalse(r["model_training_or_production_cutover_allowed"])
    def test_holds_retained_not_interpolated(self):
        temp,root,pin=sample(states=("HOLD","HOLD","READY"))
        with temp:
            r=scan_with_pin(root,pin)
            x=r["longest_original_hold_episode"]
            self.assertEqual((x["first_index"],x["last_index"],x["cycle_count"]), (0,1,2))
            self.assertEqual(x["fixed_calendar_duration_ms"],30000)
    def test_missing_status_is_failure_not_reward(self):
        temp,root,pin=sample(states=("READY","MISSING","HOLD"))
        with temp:
            r=scan_with_pin(root,pin)
            self.assertEqual(r["cycle_classes"]["MISSING_OR_FAILED"],1)
            self.assertEqual(r["cycle_classes"]["READY"],1)
            self.assertEqual(r["cycle_classes"]["ORIGINAL_HOLD"],1)
    def test_age_to_next_publication_sla_breach(self):
        temp,root,pin=sample(states=("HOLD",))
        with temp:
            first=t.sha((root/"cycle-000-receipt.json").read_bytes())
            append(root,1,first,state="HOLD",pub_delay=16700)
            r=scan_with_pin(root,pin)
            self.assertEqual(r["cross_publication_asof_age_ms"]["maximum"],31700)
            bad=r["cross_publication_asof_age_ms"]["violations"]
            self.assertEqual((bad[0]["previous_cycle"],bad[0]["next_cycle"]),(0,1))
            self.assertEqual(len(bad),1)
    def test_status_at_publication_not_enough_for_intercycle_sla(self):
        temp,root,pin=sample(states=("READY",))
        with temp:
            first=t.sha((root/"cycle-000-receipt.json").read_bytes())
            append(root,1,first,pub_delay=16200)
            r=scan_with_pin(root,pin)
            self.assertEqual(r["publication_age_ms"]["maximum"],16200)
            self.assertEqual(r["cross_publication_asof_age_ms"]["maximum"],31200)
            self.assertIn("CROSS_PUBLICATION_30S_FRESHNESS_BREACH",r["release_blockers"])
    def test_forged_false_ready_with_missing_probes_fails(self):
        temp,root,pin=sample(states=("HOLD",))
        with temp:
            p=root/"cycle-000-status.json";s=json.loads(p.read_bytes())
            s["state"]="READY_FOR_REVIEW_NOT_AUTHORIZATION"
            output(p,s)
            r=root/"cycle-000-receipt.json";rec=json.loads(r.read_bytes())
            rec["status_sha256"]=t.sha(p.read_bytes())
            rec["original_reported_state"]=s["state"]
            rec["source_ready"]=True
            output(r,rec)
            with self.assertRaisesRegex(t.Hold,"FALSE_READY"):
                scan_with_pin(root,pin)
    def test_receipt_chain_must_reconcile(self):
        temp,root,pin=sample()
        with temp:
            p=root/"cycle-001-receipt.json";r=json.loads(p.read_bytes())
            r["previous_receipt_sha256"]="e"*64;output(p,r)
            with self.assertRaisesRegex(t.Hold,"BROKEN_CYCLE"):
                scan_with_pin(root,pin)
    def test_changed_source_status_exact_hash_refused(self):
        temp,root,pin=sample(states=("READY",))
        with temp:
            p=root/"cycle-000-status.json";s=json.loads(p.read_bytes())
            s["latest_quote_age_ms"]=200;output(p,s)
            with self.assertRaisesRegex(t.Hold,"STATUS_SHA_MISMATCH"):
                scan_with_pin(root,pin)
    def test_misleading_original_source_period_refused(self):
        temp,root,pin=sample(states=("READY",))
        with temp:
            p=root/"cycle-000-status.json";s=json.loads(p.read_bytes())
            s["period_sha256"]="a"*64
            output(p,s)
            r=root/"cycle-000-receipt.json";rec=json.loads(r.read_bytes())
            rec["status_sha256"]=t.sha(p.read_bytes());output(r,rec)
            with self.assertRaisesRegex(t.Hold,"ORIGINAL_MONITOR_SCOPE_CHANGED"):
                scan_with_pin(root,pin)
    def test_inconsistent_original_probe_partition_refused(self):
        temp,root,pin=sample(states=("READY",))
        with temp:
            p=root/"cycle-000-status.json";s=json.loads(p.read_bytes())
            s["usable"]=23
            output(p,s)
            r=root/"cycle-000-receipt.json";rec=json.loads(r.read_bytes())
            rec["status_sha256"]=t.sha(p.read_bytes());rec["source_usable_probes"]=23
            output(r,rec)
            with self.assertRaisesRegex(t.Hold,"ORIGINAL_PROBE_COUNTS"):
                scan_with_pin(root,pin)
    def test_missing_interior_receipt_never_backfilled(self):
        temp,root,pin=sample(states=("READY","READY","HOLD"))
        with temp:
            (root/"cycle-001-receipt.json").unlink()
            with self.assertRaisesRegex(t.Hold,"INTERIOR_CYCLE_GAP"):
                scan_with_pin(root,pin)
    def test_premature_final_not_equated_to_480(self):
        temp,root,pin=sample(states=("READY",))
        with temp:
            output(root/"final.json",{"count":480})
            with self.assertRaisesRegex(t.Hold,"PREMATURE_FALSE_TERMINAL"):
                scan_with_pin(root,pin)
    def test_program_never_writes_over_existing_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/"private";root.mkdir(mode=0o700)
            p=root/"report.json";p.write_text("original");p.chmod(0o600)
            with self.assertRaisesRegex(t.Hold,"REPORT_MUST_BE_NEW"):
                t.write_report(p,{"fake":True})
            self.assertEqual(p.read_text(),"original")
    def test_report_is_0600_and_fsync(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/"private";root.mkdir(mode=0o700)
            p=root/"report.json"
            proof=t.write_report(p,{"state":"HOLD","trade_authority":False})
            self.assertEqual(t.sha(p.read_bytes()),proof)
            self.assertEqual(p.stat().st_mode&0o777,0o600)
    def test_public_source_rejected(self):
        temp,root,pin=sample(states=("READY",))
        with temp:
            (root/"cycle-000-status.json").chmod(0o644)
            with self.assertRaisesRegex(t.Hold,"PRIVATE_SOURCE_INVALID"):
                scan_with_pin(root,pin)
    def test_duplicate_json_fields_not_parsed(self):
        with self.assertRaisesRegex(t.Hold,"DUPLICATE_JSON_FIELD"):
            t.strict(b'{"n":1,"n":2}')
    def test_nonfinite_json_not_parsed(self):
        with self.assertRaisesRegex(t.Hold,"NONFINITE_JSON"):
            t.strict(b'{"n":NaN}')
    def test_original_plan_sha_required(self):
        temp,root,pin=sample(states=("READY",))
        with temp:
            with self.assertRaisesRegex(t.Hold,"FROZEN_480_PLAN_MISMATCH"):
                t.scan(root)

if __name__=="__main__":unittest.main()
