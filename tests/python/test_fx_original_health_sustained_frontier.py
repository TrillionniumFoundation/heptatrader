"""Adversarial tests for *partial* immutable FX original quote-health ledger audit."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from train_v2.fx_original_health_sustained_audit import (
    COHORT,N,STEP,MAX_AGE,RULE,encode,digest,IntegrityHold
)
from train_v2.fx_original_health_sustained_frontier import (
    verify_prefix, SCHEMA, PREFIX_COUNT
)

FIRST=1791870630000
PERIOD="f"*64

def save(p,obj):
    p.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    p.write_bytes(encode(obj)+b"\n")
    p.chmod(0o600)
    return digest(p.read_bytes())

def sample(index, target, last):
    at=target+2
    probe=[{"status":"USABLE","start_ms":target-60000*i}
           for i in range(24)]
    source=[{"path":"/private/original","sha256":"a"*64}]
    status={
        "schema":"heptatrader.quote-readiness-status.v1",
        "period_sha256":PERIOD,"rule":copy.deepcopy(RULE),
        "probes":probe,"counts":{"USABLE":24},"usable":24,"unusable":0,
        "source_prefixes":source,"source_prefix_digest":digest(encode(source)),
        "at_ms":at,"latest_source_quote_ms":at-1000,
        "latest_quote_age_ms":1000,"automatic_trial_registration":False,
        "trading_authority":False,"orders_sent":0,
        "reasons":[],"state":"READY_FOR_REVIEW_NOT_AUTHORIZATION",
    }
    record={
        "schema":COHORT+".cycle.v1","index":index,
        "frozen_target_ms":target,"assessment_started_ms":at,
        "record_sealed_at_ms":at+1500,"previous_receipt_sha256":last,
        "original_period_sha256":PERIOD,
        "orders_sent":0,"trade_authority":False,
        "state":"CAPTURED_ORIGINAL_HEALTH","issues":[],
        "status_sha256":None,"original_asof_ms":at,
        "source_ready":True,"source_usable_probes":24,
        "source_prefix_digest":status["source_prefix_digest"],
        "published_at_ms":at+1200,
        "publisher_eligible":True,
    }
    return status,record

def make_fixture(path,n=4):
    path.mkdir(mode=0o700)
    schedule=[FIRST+i*STEP for i in range(N)]
    plan={
        "schema":COHORT+".plan.v1",
        "full_snapshots_count":N,"original_monitor_rules":copy.deepcopy(RULE),
        "candidate_assessment_cadence_ms":STEP,
        "maximum_status_age_ms":MAX_AGE,
        "maximum_schedule_delay_ms":3000,
        "first_cycle_start_ms":FIRST,"cycle_schedule_ms":schedule,
        "no_training_or_trading":True,
        "no_production_monitor_cutover":True,
        "trade_authority":False,"broker_requests_or_orders_sent":0,
        "period_sha256":PERIOD
    }
    ph=save(path/"plan.json",plan)
    save(path/"worker-start.json",{
        "schema":COHORT+".worker.v1","plan_sha256":ph,
        "start_ms":FIRST-10000,"no_restart":True,
        "production_monitor_not_modified":True,"broker_orders_sent":0,
    })
    save(path/"sandbox.json",{"schema":"test","landlock_enforced":True,"seccomp_enforced":True,"socket_creation_denied":True,"bearer_read_denied":True,"only_output_writable":True,"output_write_verified":True})
    chain="0"*64
    for i in range(n):
        s,rec=sample(i,schedule[i],chain)
        h=save(path/("cycle-%02d-status.json"%i),s)
        rec["status_sha256"]=h
        chain=save(path/("cycle-%02d-receipt.json"%i),rec)
    return path

def mutate(path,filename,fn):
    p=path/filename
    obj=json.loads(p.read_bytes())
    fn(obj)
    save(p,obj)

class PrefixTests(unittest.TestCase):
    def test_frozen_prefix_not_full_campaign(self):
        with tempfile.TemporaryDirectory() as t:
            root=make_fixture(Path(t)/"root")
            result=verify_prefix(root,4)
            self.assertEqual(result["contiguous_prefix_cycles_checked"],4)
            self.assertEqual(result["future_cycles_NOT_verified"],476)
            self.assertEqual(result["source_ready_snapshots"],4)
            self.assertEqual(result["worst_published_original_source_age_ms"],1200)
            self.assertEqual(result["worst_status_age_at_next_publication_ms"],16200)
            self.assertFalse(result["cohort_complete"])
            self.assertFalse(result["production_monitor_cadence_qualified"])
            self.assertFalse(result["trade_authority"])
    def test_missing_causal_source_denied(self):
        with tempfile.TemporaryDirectory() as t:
            root=make_fixture(Path(t)/"root")
            (root/"cycle-02-status.json").unlink()
            with self.assertRaises((IntegrityHold,FileNotFoundError)):
                verify_prefix(root,4)
    def test_modifying_original_receipt_denied(self):
        with tempfile.TemporaryDirectory() as t:
            root=make_fixture(Path(t)/"root")
            mutate(root,"cycle-01-receipt.json",
                   lambda s: s.update(source_ready=False))
            with self.assertRaisesRegex(IntegrityHold,"CHANGED|SOURCE"):
                verify_prefix(root,4)
    def test_broken_rolling_hash_denied(self):
        with tempfile.TemporaryDirectory() as t:
            root=make_fixture(Path(t)/"root")
            mutate(root,"cycle-02-receipt.json",
                   lambda s: s.update(previous_receipt_sha256="e"*64))
            with self.assertRaisesRegex(IntegrityHold,"HISTORICAL_CYCLE_ORDER"):
                verify_prefix(root,4)
    def test_altered_original_health_probe_denied(self):
        with tempfile.TemporaryDirectory() as t:
            root=make_fixture(Path(t)/"root")
            mutate(root,"cycle-01-status.json",
                   lambda s: s["probes"][0].update(status="PATH_GAP"))
            with self.assertRaisesRegex(IntegrityHold,"ORIGINAL_SOURCE_BYTES"):
                verify_prefix(root,4)
    def test_future_only_count_cannot_upgrade(self):
        with tempfile.TemporaryDirectory() as t:
            root=make_fixture(Path(t)/"root")
            for c in (0,-1,480,True):
                with self.subTest(c=c),self.assertRaisesRegex(IntegrityHold,
                                                   "INCOMPLETE_PREFIX_COUNT"):
                    verify_prefix(root,c)
    def test_fake_full_final_is_not_accepted_as_partial(self):
        with tempfile.TemporaryDirectory() as t:
            root=make_fixture(Path(t)/"root")
            save(root/"final.json",{"schema":COHORT+".final.v1"})
            with self.assertRaisesRegex(IntegrityHold,"FINAL_ALREADY_EXISTS"):
                verify_prefix(root,4)
    def test_widened_original_threshold_refused(self):
        with tempfile.TemporaryDirectory() as t:
            root=make_fixture(Path(t)/"root")
            mutate(root,"plan.json",
                   lambda s:s["original_monitor_rules"].update(minimum_usable=1))
            with self.assertRaisesRegex(IntegrityHold,"PRESEALED_PRODUCTION_SCOPE_CHANGED"):
                verify_prefix(root,4)
    def test_source_quote_at_ms_mismatch(self):
        with tempfile.TemporaryDirectory() as t:
            root=make_fixture(Path(t)/"root")
            mutate(root,"cycle-01-status.json",
                   lambda s:s.update(latest_quote_age_ms=3000))
            with self.assertRaisesRegex(IntegrityHold,"ORIGINAL_SOURCE_BYTES"):
                verify_prefix(root,4)
    def test_cycle_late_denied(self):
        with tempfile.TemporaryDirectory() as t:
            root=make_fixture(Path(t)/"root")
            mutate(root,"cycle-00-receipt.json",
                   lambda s:s.update(assessment_started_ms=FIRST+6000))
            with self.assertRaisesRegex(IntegrityHold,"HISTORICAL_CYCLE_SCHEDULE"):
                verify_prefix(root,4)
    def test_refuse_public_root(self):
        with tempfile.TemporaryDirectory() as t:
            root=make_fixture(Path(t)/"root")
            root.chmod(0o755)
            with self.assertRaisesRegex(IntegrityHold,"PRIVATE_ROOT_INVALID"):
                verify_prefix(root,4)
    def test_refuse_nonfinite_and_duplicate_json(self):
        from train_v2.fx_original_health_sustained_audit import strict
        for raw in (b'{"x":NaN}',b'{"x":1,"x":2}'):
            with self.assertRaises(IntegrityHold):strict(raw)
    def test_false_sandbox_controls_refused(self):
        with tempfile.TemporaryDirectory() as t:
            root=make_fixture(Path(t)/"root")
            mutate(root,"sandbox.json",
                   lambda s:s.update(socket_creation_denied=False))
            with self.assertRaisesRegex(IntegrityHold,"SOURCE_SANDBOX_CONTROLS"):
                verify_prefix(root,4)

    def test_user_actor_permission_never_granted(self):
        with tempfile.TemporaryDirectory() as t:
            root=make_fixture(Path(t)/"root")
            r=verify_prefix(root,4)
            for key in ("model_promotion_allowed","trade_authority","market_quote_signed_by_ib",
                        "independent_hardware_timestamp_witness",
                        "actual_execution_after_cost_pnl_attested",
                        "production_monitor_cadence_qualified"):
                self.assertIs(r[key],False)

if __name__=="__main__":unittest.main()
