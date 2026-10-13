"""Adversarial tests for a separately preregistered, 24-cycle health canary."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from train_v2.fx_health_24cycle_ops_audit import (
    GateHold,PLAN_SCHEMA,EXPECTED_CODE_SHA,UNIT,qualify,run,sha,canonical,
    exact_private_bytes,read_strict,EXPECTED_PAPER_PERIOD_SHA,EXPECTED_SOURCE_RULE,
)
from train_v2.source_health_cache_sla_review import AuditHold

def fixture():
    start=1791857785804
    events=[]
    for i in range(24):
        if i==0:
            t=start;age=18314
        else:
            t=start+19314+(i-1)*15000;age=1800
        events.append({"schema":"heptatrader.source-readiness-cadence-lab.v1",
            "cycle":i+1,"assess_ms":age,
            "source_asof_ms":t,"published_ms":t+age,
            "published_source_age_ms":age,
            "state":"READY_FOR_REVIEW_NOT_AUTHORIZATION","usable":24,
            "trade_authority":False,"orders_sent":0})
    cache={"schema":"heptatrader.readiness-verified-sealed-cache-counters.v1",
       "archive_inventories_full_sha256_reverified":24,
       "sealed_decoding_reused":256*23,
       "sealed_hits_bytes_reverified_at_use":256*23,
       "sealed_decoding_strictly_parsed":256,
       "unsealed_or_extra_strictly_reparsed":24*5,
       "cached_sealed_segments":256,
       "cached_original_quote_records":116289,
       "cache_is_not_independent_broker_signature":True,
       "trade_authority":False,"broker_orders_sent":0}
    plan={"schema":PLAN_SCHEMA,"frozen_at_ms":start-1000,
          "planned_cycles":24,"cpu_quota_percent":25,
          "requested_cadence_seconds":15,
          "source_sha256":dict(EXPECTED_CODE_SHA),
          "sandbox_only":True,"broker_orders_sent":0,"trade_authority":False}
    final={"schema":"heptatrader.quote-readiness-status.v1",
           "at_ms":events[-1]["source_asof_ms"],
           "period_sha256":EXPECTED_PAPER_PERIOD_SHA,
           "rule":dict(EXPECTED_SOURCE_RULE),
           "state":"READY_FOR_REVIEW_NOT_AUTHORIZATION",
           "usable":24,"unusable":0,
           "probes":[{"status":"USABLE"} for _ in range(24)],
           "counts":{"USABLE":24},
           "reasons":[],
           "latest_source_quote_ms":events[-1]["source_asof_ms"]-1000,
           "latest_quote_age_ms":1000,
           "source_prefixes":[{"sha256":"a"*64,"bytes":200}],
           "source_prefix_digest":None,
           "automatic_trial_registration":False,
           "trading_authority":False,"orders_sent":0}
    final["source_prefix_digest"]=sha(canonical(final["source_prefixes"]))
    journal=b"\n".join(canonical(x) for x in events+[cache])+b"\n"
    return plan,events,cache,final,journal

def sources():
    root=Path(__file__).resolve().parents[2]/"research"/"neuron_learning"/"laya_cell"
    return {key:root/key for key in EXPECTED_CODE_SHA}

class CandidateAuditTests(unittest.TestCase):
    def data(self):
        plan,ev,cache,st,j=fixture()
        return qualify(canonical(plan),sources(),j,canonical(st))
    def test_good_source_only_result_not_trading(self):
        r=self.data()
        self.assertEqual(r["observed_cycles"],24)
        self.assertTrue(r["continuous_snapshot_freshness_pass"])
        self.assertEqual(r["cache_full_archive_rechecks"],24)
        self.assertFalse(r["ready_for_unattended_production_cutover"])
        self.assertEqual(r["broker_orders_sent"],0)
        self.assertFalse(r["trade_authority"])
    def test_tampered_precommit_program_hash_rejected(self):
        p,e,c,s,j=fixture();p["source_sha256"]["source_readiness.py"]="1"*64
        with self.assertRaisesRegex(GateHold,"FROZEN_SOURCE_SET_CHANGED"):
            qualify(canonical(p),sources(),j,canonical(s))
    def test_tampered_precommit_clock_rejected(self):
        p,e,c,s,j=fixture();p["frozen_at_ms"]=e[0]["source_asof_ms"]+10
        with self.assertRaisesRegex(GateHold,"PRECOMMIT_NOT_BEFORE"):
            qualify(canonical(p),sources(),j,canonical(s))
    def test_wider_cpu_budget_rejected(self):
        p,e,c,s,j=fixture();p["cpu_quota_percent"]=100
        with self.assertRaisesRegex(GateHold,"SLA_PRECOMMIT_CHANGED"):
            qualify(canonical(p),sources(),j,canonical(s))
    def test_fewer_cycles_rejected(self):
        p,e,c,s,j=fixture();p["planned_cycles"]=8
        with self.assertRaisesRegex(GateHold,"SLA_PRECOMMIT_CHANGED"):
            qualify(canonical(p),sources(),j,canonical(s))
    def test_broker_trafficking_claim_refused(self):
        p,e,c,s,j=fixture();c["broker_orders_sent"]=1
        forged=b"\n".join(canonical(x) for x in e+[c])+b"\n"
        with self.assertRaises(AuditHold):
            qualify(canonical(p),sources(),forged,canonical(s))
    def test_archive_unverified_cache_hit_refused(self):
        p,e,c,s,j=fixture();c["sealed_hits_bytes_reverified_at_use"]=0
        forged=b"\n".join(canonical(x) for x in e+[c])+b"\n"
        with self.assertRaises(AuditHold):
            qualify(canonical(p),sources(),forged,canonical(s))
    def test_missing_cycle_refused(self):
        p,e,c,s,j=fixture()
        forged=b"\n".join(canonical(x) for x in e[:15]+e[16:]+[c])+b"\n"
        with self.assertRaises(AuditHold):
            qualify(canonical(p),sources(),forged,canonical(s))
    def test_forged_final_status_time_refused(self):
        p,e,c,s,j=fixture();s["at_ms"]+=20000
        with self.assertRaisesRegex(GateHold,"FINAL_ORIGINAL_SOURCE_SNAPSHOT_NOT_CAUSAL"):
            qualify(canonical(p),sources(),j,canonical(s))
    def test_stale_final_metadata_refused(self):
        p,e,c,s,j=fixture();s["usable"]=16
        with self.assertRaisesRegex(GateHold,"FINAL_ORIGINAL_SOURCE_SNAPSHOT_NOT_CAUSAL"):
            qualify(canonical(p),sources(),j,canonical(s))
    def test_source_prefix_identity_refused(self):
        p,e,c,s,j=fixture();s["source_prefixes"][0]["sha256"]="0"*64
        with self.assertRaisesRegex(GateHold,"SOURCE_PROVENANCE_DIGEST_CHANGED"):
            qualify(canonical(p),sources(),j,canonical(s))
    def test_original_source_new_auth_state_refused(self):
        p,e,c,s,j=fixture();s["automatic_trial_registration"]=True
        with self.assertRaisesRegex(GateHold,"FINAL_ORIGINAL_SOURCE_SNAPSHOT_NOT_CAUSAL"):
            qualify(canonical(p),sources(),j,canonical(s))
    def test_json_key_duplicate_refused(self):
        with self.assertRaisesRegex(GateHold,"DUPLICATE_JSON_KEY"):
            read_strict(b'{"cycle":1,"cycle":2}')
    def test_nonfinite_json_refused(self):
        with self.assertRaisesRegex(GateHold,"NONFINITE_JSON"):
            read_strict(b'{"cycle":NaN}')
    def test_public_original_plan_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"plan.json";p.write_text("{}");p.chmod(0o644)
            with self.assertRaisesRegex(GateHold,"ORIGINAL_PRIVATE_SOURCE_SCOPE"):
                exact_private_bytes(p)
    def test_no_existing_receipt_overwrite(self):
        p,e,c,s,j=fixture()
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/"private";root.mkdir(mode=0o700)
            plan=root/"plan.json";plan.write_bytes(canonical(p));plan.chmod(0o600)
            status=root/"status.json";status.write_bytes(canonical(s));status.chmod(0o600)
            audit=root/"independent-operational-audit.json"
            audit.write_text("OLD");audit.chmod(0o600)
            with self.assertRaisesRegex(GateHold,"SLA_AUDIT_ALREADY_EXISTS_NO_OVERWRITE"):
                run(root,plan,sources(),status,journal_data=j)
            self.assertEqual(audit.read_text(),"OLD")
    def test_real_synthetic_payload_saved_privately(self):
        p,e,c,s,j=fixture()
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/"private";root.mkdir(mode=0o700)
            plan=root/"plan.json";plan.write_bytes(canonical(p));plan.chmod(0o600)
            status=root/"status.json";status.write_bytes(canonical(s));status.chmod(0o600)
            r=run(root,plan,sources(),status,journal_data=j)
            self.assertEqual(r["observed_cycles"],24)
            f=root/"independent-operational-audit.json"
            self.assertEqual(f.stat().st_mode & 0o777,0o600)
            self.assertEqual((root/"original-journal.txt").read_bytes(),j)

    def test_forged_ready_probe_counts_not_accepted(self):
        p,e,c,s,j=fixture()
        s["probes"][0]["status"]="PATH_GAP"
        with self.assertRaisesRegex(GateHold,"FINAL_MONITOR_PROBES_OR_PERIOD_NOT_ATTESTABLE"):
            qualify(canonical(p),sources(),j,canonical(s))
    def test_wrong_period_scope_not_accepted(self):
        p,e,c,s,j=fixture()
        s["period_sha256"]="0"*64
        with self.assertRaisesRegex(GateHold,"FINAL_MONITOR_PROBES_OR_PERIOD_NOT_ATTESTABLE"):
            qualify(canonical(p),sources(),j,canonical(s))
    def test_changed_original_rules_not_accepted(self):
        p,e,c,s,j=fixture()
        s["rule"]["source_ttl_ms"]=90000
        with self.assertRaisesRegex(GateHold,"FINAL_MONITOR_PROBES_OR_PERIOD_NOT_ATTESTABLE"):
            qualify(canonical(p),sources(),j,canonical(s))
    def test_original_quote_age_disagreement_not_accepted(self):
        p,e,c,s,j=fixture()
        s["latest_quote_age_ms"]=1001
        with self.assertRaisesRegex(GateHold,"FINAL_ORIGINAL_QUOTE_EXPIRED_OR_AGE_FORGED"):
            qualify(canonical(p),sources(),j,canonical(s))
    def test_stale_last_quote_even_if_24_ready_refused(self):
        p,e,c,s,j=fixture()
        s["latest_quote_age_ms"]=5500
        s["latest_source_quote_ms"]=s["at_ms"]-5500
        with self.assertRaisesRegex(GateHold,"FINAL_ORIGINAL_QUOTE_EXPIRED_OR_AGE_FORGED"):
            qualify(canonical(p),sources(),j,canonical(s))

if __name__=="__main__":unittest.main()
