"""Adversarial tests for byte-exact original-PAPER health source monitoring."""
from __future__ import annotations
import json,os,tempfile
from contextlib import redirect_stdout
import io
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

from train_v2 import fx_original_health_fullcycle as fullcycle
from train_v2 import fx_original_health_sustained as sustained

from train_v2.fx_original_health_fullcycle import (
    AuditHold, canonical, digest, owner_dir, write_once, verify_status,
    strict, private_source, STEP_MS, MAX_SOURCE_AGE_MS, RULE
)
from train_v2.fx_original_health_fullcycle_audit import (
    IntegrityHold, check_status, verify_all, persist,
    SCHEMA, COHORT, N, STEP, MAX_AGE
)

PERIOD="a"*64
START=1791870000000

def status_for(i, *, state="READY_FOR_REVIEW_NOT_AUTHORIZATION"):
    asof=START+i*STEP
    healthy=state=="READY_FOR_REVIEW_NOT_AUTHORIZATION"
    counts={"USABLE":24} if healthy else {"USABLE":20,"PATH_GAP":4}
    items=[{"status":"USABLE","start_ms":asof-k*60000} for k in range(24)]
    if not healthy:
        for k in range(4):items[k]["status"]="PATH_GAP"
    prefixes=[{"segment":"s","prefix_sha256":"f"*64,"bytes":1200}]
    return {
        "schema":"heptatrader.quote-readiness-status.v1",
        "period_sha256":PERIOD,"rule":RULE,
        "state":state,"at_ms":asof,
        "counts":counts,"probes":items,
        "usable":counts["USABLE"],"unusable":24-counts["USABLE"],
        "source_prefixes":prefixes,
        "source_prefix_digest":digest(canonical(prefixes)),
        "latest_source_quote_ms":asof-1000,
        "latest_quote_age_ms":1000,
        "automatic_trial_registration":False,
        "trading_authority":False,
        "orders_sent":0,
        "reasons":[] if healthy else ["INSUFFICIENT_ENDPOINT_AND_PATH_COVERAGE"],
    }

def fixture(root,*,delay=8000,fail_at=None,source_hold=None):
    root=Path(root);root.mkdir(mode=0o700)
    plan={
        "schema":COHORT+".plan.v1",
        "registered_at_ms":START-120000,
        "first_cycle_start_ms":START,
        "cycle_schedule_ms":[START+i*STEP for i in range(N)],
        "full_snapshots_count":N,
        "original_monitor_rules":RULE,
        "candidate_assessment_cadence_ms":STEP,
        "maximum_status_age_ms":MAX_AGE,
        "trade_authority":False,
        "no_production_monitor_cutover":True,
        "broker_requests_or_orders_sent":0,
        "period_sha256":PERIOD,
    }
    plan_sha=write_once(root/"plan.json",canonical(plan)+b"\n")
    write_once(root/"worker-start.json",canonical({
        "plan_sha256":plan_sha,"no_restart":True,
        "production_monitor_not_modified":True,"broker_orders_sent":0,
    })+b"\n")
    write_once(root/"sandbox.json",b'{"sandbox":true}\n')
    previous="0"*64
    results=[]
    for i in range(N):
        at=START+i*STEP
        if i==fail_at:
            event={
                "state":"CENSORED_MISSED_FROZEN_CYCLE",
                "issues":["MISSED_FROZEN_SCHEDULE"],
                "status_sha256":None,
                "original_asof_ms":None,
                "publisher_eligible":False,
                "source_ready":False,
            }
        else:
            s=status_for(i, state=(
                "HOLD_NEW_FUSION_WINDOW" if i==source_hold
                else "READY_FOR_REVIEW_NOT_AUTHORIZATION"))
            b=canonical(s)+b"\n"
            hs=write_once(root/("cycle-%02d-status.json"%i),b)
            event={
                "state":"CAPTURED_ORIGINAL_HEALTH","issues":[],
                "status_sha256":hs,"original_asof_ms":at,
                "source_ready":s["state"]=="READY_FOR_REVIEW_NOT_AUTHORIZATION",
                "source_usable_probes":s["usable"],
                "source_prefix_digest":s["source_prefix_digest"],
                "published_at_ms":at+delay,
                "publisher_eligible":True,
            }
        event.update({
            "schema":COHORT+".cycle.v1","index":i,
            "frozen_target_ms":at,
            "assessment_started_ms":at,
            "record_sealed_at_ms":at+delay+100,
            "previous_receipt_sha256":previous,
            "original_period_sha256":PERIOD,
            "trade_authority":False,"orders_sent":0,
        })
        previous=write_once(root/("cycle-%02d-receipt.json"%i),canonical(event)+b"\n")
        results.append({
            "index":i,"status":event["state"],
            "receipt_sha256":previous,"source_ready":event["source_ready"],
        })
    write_once(root/"final.json",canonical({
        "schema":COHORT+".final.v1","plan_sha256":plan_sha,
        "receipt_chain_head":previous,"cycles":results,
        "full_snapshots_written":N-(fail_at is not None),
        "no_prod_monitor_cutover":True,
        "production_trading_grant":False,"broker_orders_sent":0,
    })+b"\n")
    return root

class FullCycleTests(unittest.TestCase):
    def test_two_distinct_counters_and_market_holds(self):
        s=status_for(1,state="HOLD_NEW_FUSION_WINDOW")
        self.assertEqual(verify_status(s,PERIOD,START+STEP),[])
        self.assertEqual(check_status(s,PERIOD,{
            "assessment_started_ms":START+STEP,
            "published_at_ms":START+STEP+8000})["state"],"HOLD_NEW_FUSION_WINDOW")
    def test_wrong_period_hold(self):
        s=status_for(0);s["period_sha256"]="b"*64
        self.assertIn("HEALTH_PERIOD_CHANGED",verify_status(s,PERIOD,START))
        with self.assertRaisesRegex(IntegrityHold,"ORIGINAL_READINESS_SCOPE_CHANGED"):
            check_status(s,PERIOD,{"assessment_started_ms":START,"published_at_ms":START+8000})
    def test_24_probes_inconsistent(self):
        s=status_for(0);s["counts"]["USABLE"]=23
        self.assertIn("PROBE_COUNTS_CHANGED",verify_status(s,PERIOD,START))
    def test_source_prefix_changed(self):
        s=status_for(0);s["source_prefixes"][0]["bytes"]+=1
        self.assertIn("PREFIXES_SHA_CHANGED",verify_status(s,PERIOD,START))
    def test_false_ready_with_expired_quote(self):
        s=status_for(0);s["latest_quote_age_ms"]=6000
        s["latest_source_quote_ms"]=s["at_ms"]-6000
        self.assertIn("READY_CLAIM_WITH_UNQUALIFIED_IB_SOURCE",verify_status(s,PERIOD,START))
        with self.assertRaisesRegex(IntegrityHold,"FALSE_READY"):
            check_status(s,PERIOD,{"assessment_started_ms":START,"published_at_ms":START+8000})
    def test_duplicate_json_and_nonfinite(self):
        with self.assertRaisesRegex(AuditHold,"DUPLICATE_JSON_KEY"):
            strict(b'{"x":1,"x":2}')
        with self.assertRaisesRegex(AuditHold,"NONFINITE_JSON"):
            strict(b'{"x":NaN}')
    def test_existing_evidence_not_overwritten(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)/"owner"
            root.mkdir(mode=0o700)
            p=root/"evidence.json"
            write_once(p,b'{"original":1}\n')
            with self.assertRaises(FileExistsError):
                write_once(p,b'{"original":2}\n')
            self.assertEqual(p.read_bytes(),b'{"original":1}\n')
    def test_sandbox_bad_file_mode_refused(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/"status.json";p.write_text("{}");p.chmod(0o644)
            with self.assertRaisesRegex(AuditHold,"ARCHIVE_SOURCE_INVALID"):
                private_source(p)
    def test_24_exact_complete_original_jsons_pass(self):
        with tempfile.TemporaryDirectory() as t:
            root=fixture(Path(t)/"cohort")
            result=verify_all(root)
            self.assertEqual(result["complete_original_health_json_count"],24)
            self.assertEqual(result["source_ready_cycle_count"],24)
            self.assertEqual(result["state"],"PASS_FULLBYTE_ORIGINAL_SOURCE_CUSTODY_TIMING_ONLY")
            self.assertEqual(result["worst_source_age_just_before_next_publication_ms"],23000)
            self.assertFalse(result["prod_monitor_cadence_switch_approved"])
            self.assertFalse(result["trade_authority"])
    def test_underlying_source_hold_not_reclassified(self):
        with tempfile.TemporaryDirectory() as t:
            r=verify_all(fixture(Path(t)/"cohort",source_hold=7))
            self.assertEqual(r["source_ready_cycle_count"],23)
            self.assertEqual(r["source_hold_cycle_count"],1)
            self.assertFalse(r["source_100pct_ready_attested"])
    def test_missing_original_health_is_hold_not_success(self):
        with tempfile.TemporaryDirectory() as t:
            r=verify_all(fixture(Path(t)/"cohort",fail_at=7))
            self.assertEqual(r["complete_original_health_json_count"],23)
            self.assertEqual(r["state"],"HOLD_INCOMPLETE_OR_STALE_ORIGINAL_SOURCE_SNAPSHOTS")
    def test_public_original_status_is_refused(self):
        with tempfile.TemporaryDirectory() as t:
            root=fixture(Path(t)/"cohort")
            (root/"cycle-03-status.json").chmod(0o644)
            with self.assertRaisesRegex(IntegrityHold,"ORIGINAL_BYTES_MODE_OR_SIZE"):
                verify_all(root)
    def test_status_mutation_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            root=fixture(Path(t)/"cohort")
            p=root/"cycle-05-status.json"
            s=json.loads(p.read_text());s["usable"]=1
            p.write_bytes(canonical(s)+b"\n")
            with self.assertRaisesRegex(IntegrityHold,"SNAPSHOT_SHA_NOT_ORIGINAL"):
                verify_all(root)
    def test_missing_producer_receipt_refused(self):
        with tempfile.TemporaryDirectory() as t:
            root=fixture(Path(t)/"cohort")
            (root/"cycle-23-receipt.json").unlink()
            with self.assertRaises(FileNotFoundError):
                verify_all(root)
    def test_slow_publishing_must_be_hold(self):
        with tempfile.TemporaryDirectory() as t:
            r=verify_all(fixture(Path(t)/"cohort",delay=17000))
            self.assertFalse(r["all_decision_time_snapshot_ages_within_30s"])
            self.assertEqual(r["state"],"HOLD_INCOMPLETE_OR_STALE_ORIGINAL_SOURCE_SNAPSHOTS")
    def test_no_sneaky_new_trading_grant_in_status(self):
        with tempfile.TemporaryDirectory() as t:
            root=fixture(Path(t)/"cohort")
            p=root/"cycle-01-status.json"
            s=json.loads(p.read_text());s["trading_authority"]=True
            p.write_bytes(canonical(s)+b"\n")
            with self.assertRaises(IntegrityHold):
                verify_all(root)
    def test_new_report_only(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)/"report";root.mkdir(mode=0o700)
            p=root/"done.json"
            persist({"schema":SCHEMA,"trade_authority":False},p)
            with self.assertRaisesRegex(IntegrityHold,"AUDIT_OUTPUT_MUST_BE_NEW"):
                persist({"schema":SCHEMA,"trade_authority":True},p)

class SharedCohortRunnerTests(unittest.TestCase):
    def setUp(self):
        box=tempfile.TemporaryDirectory()
        self.addCleanup(box.cleanup)
        self.directory=Path(box.name)
        self.next_cohort=0
        self.period=self.directory/"period.json"
        self.period.write_bytes(canonical({"execution_domain":"PAPER"})+b"\n")
        self.config=self.directory/"config.json"
        self.config.write_bytes(canonical({
            "schema":"heptatrader.laya-resident-config.v1",
            "automatic_trading":False,"period_path":str(self.period),
            "period_sha256":digest(self.period.read_bytes()),
            "feed_directory":"/home/alex/.local/state/heptatrader/synthetic-source",
        })+b"\n")
        self.installed=self.directory/"installed-monitor.py"
        self.installed.write_text("# synthetic monitor fixture only\n")
        for path in (self.period,self.config,self.installed):path.chmod(0o600)

    def make_plan(self,module):
        root=self.directory/("cohort-%d"%self.next_cohort)
        self.next_cohort+=1
        with patch.object(fullcycle,"now_ms",return_value=START):
            result=module.preseal(root,self.config,self.installed)
        return root,json.loads((root/"plan.json").read_text()),result

    def test_both_entries_preserve_exact_cohort_and_cli_identity(self):
        for module in (fullcycle,sustained):
            with self.subTest(entry=module.__name__):
                root,plan,result=self.make_plan(module)
                self.assertEqual(plan["schema"],module.SCHEMA+".plan.v1")
                self.assertEqual(plan["full_snapshots_count"],module.N)
                self.assertEqual(result["full_snapshots_expected"],module.N)
                first=plan["first_cycle_start_ms"]
                self.assertGreaterEqual(first,START+module.MIN_LEAD_MS)
                self.assertLess(first,START+module.MIN_LEAD_MS+STEP_MS)
                self.assertEqual(plan["cycle_schedule_ms"],
                                 [first+i*STEP_MS for i in range(module.N)])
                self.assertEqual(plan["cohort_runner_sha256"],
                                 digest(Path(module.__file__).read_bytes()))
                self.assertEqual(plan["cohort_core_sha256"],fullcycle.code_sha())
                self.assertEqual(module.verify_plan(root)[1],plan)
                output=io.StringIO()
                with patch.object(sys,"argv",["cohort","--mode","verify","--root",str(root)]), \
                     patch.object(fullcycle.os,"umask"),redirect_stdout(output):
                    module.main()
                self.assertEqual(json.loads(output.getvalue())["expected_cycles"],module.N)
                self.assertFalse(plan["trade_authority"])
                self.assertEqual(plan["broker_requests_or_orders_sent"],0)

    def test_new_plan_requires_both_own_entry_and_shared_core_pins(self):
        for field in ("cohort_runner_sha256","cohort_core_sha256"):
            for replacement in (None,"0"*64):
                with self.subTest(field=field,replacement=replacement):
                    root,plan,_=self.make_plan(sustained)
                    if replacement is None:del plan[field]
                    else:plan[field]=replacement
                    (root/"plan.json").write_bytes(canonical(plan)+b"\n")
                    with self.assertRaisesRegex(AuditHold,"FROZEN_SOURCE_PLAN_INVALID"):
                        sustained.verify_plan(root)

    def test_changed_core_is_refused_even_when_entry_bytes_match(self):
        root,plan,_=self.make_plan(sustained)
        original_read=Path.read_bytes
        core_path=Path(fullcycle.__file__)
        def changed_core(path):
            raw=original_read(path)
            return raw+b"\n# changed shared implementation\n" if path==core_path else raw
        with patch.object(Path,"read_bytes",changed_core):
            self.assertEqual(sustained.code_sha(),plan["cohort_runner_sha256"])
            with self.assertRaisesRegex(AuditHold,"FROZEN_SOURCE_PLAN_INVALID"):
                sustained.verify_plan(root)

    def test_other_cohort_cannot_verify_the_same_plan(self):
        root,_,_=self.make_plan(fullcycle)
        with self.assertRaisesRegex(AuditHold,"FROZEN_SOURCE_PLAN_INVALID"):
            sustained.verify_plan(root)
        for name in ("AuditHold","verify_status","canonical","digest","owner_dir",
                     "write_once","private_source","strict","wait_to","require"):
            self.assertIs(getattr(sustained,name),getattr(fullcycle,name),name)


if __name__=="__main__":unittest.main()
