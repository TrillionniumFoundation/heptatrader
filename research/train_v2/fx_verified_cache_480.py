"""Finite 480-cycle *verified-sealed-cache* original health custody at 25% CPU.

The existing original source/archive semantics, 24-probe health rules, and
native trading systems are unchanged. This is a NEW prospective private
research sample, not a production health-service cutover nor model admission.
"""
from __future__ import annotations
import argparse
import json
import hashlib
import os
from pathlib import Path
import time
from collections import Counter

from historical_training.common import sha
from laya_cell.integrity import read_json
from laya_cell.source_readiness import RULE, assess
from laya_cell.verified_sealed_readiness_cache import VerifiedSealedReadinessCache
from .fx_original_health_sustained import (
    AuditHold, N, STEP_MS, MAX_LATE_MS, MAX_SOURCE_AGE_MS, MIN_LEAD_MS,
    now_ms, canonical, code_pins as archive_code_pins, digest, owner_dir,
    write_once, private_source, strict, verify_status, wait_to, require,
)

SCHEMA = "heptatrader.fx-verified-cache-480-original-readonly.v1"
ORIGINAL_MONITOR_SHA = "63bcef1a6d8bc22e3c2fb10d1dcebc1457559538e6a959004e61eeb88c09c320"
FAST_MONITOR_SHA = "8cbedb6a5f792891f0141f7b2ae0da91a7738080cd14c45995436ed55473c309"
VERIFIED_CACHE_SHA = "1d1bdea5702d1fab8c9412eb2cbf0d224dd34d2736d4ab822e71bff5b9acb61c"
REPORTING_BUDGET_MS = 30000

def source_file():
    return Path(__file__).parent.parent/"neuron_learning/laya_cell/source_readiness.py"
def cache_file():
    return Path(__file__).parent.parent/"neuron_learning/laya_cell/verified_sealed_readiness_cache.py"
def original_observer(path):
    p = Path(path)
    require(p.is_absolute() and not p.is_symlink() and
            p.is_file() and sha(p)==ORIGINAL_MONITOR_SHA,
            "INSTALLED_MONITOR_IDENTITY_CHANGED")
    return p
def verify_candidate():
    require(sha(source_file())==FAST_MONITOR_SHA
            and sha(cache_file())==VERIFIED_CACHE_SHA
            and N==480 and STEP_MS==15000 and
            MAX_SOURCE_AGE_MS==30000 and MAX_LATE_MS==3000,
            "CACHE_CANDIDATE_OR_SOURCE_CONTRACT_CHANGED")
    return True

def preseal(*,root,config_file,installed_source):
    root = Path(root)
    config_file=Path(config_file)
    verify_candidate()
    original_observer(installed_source)
    cfg=read_json(config_file)
    period=Path(cfg["period_path"])
    require(cfg.get("schema")=="heptatrader.laya-resident-config.v1"
            and cfg.get("automatic_trading") is False
            and cfg.get("period_sha256")==sha(period)
            and read_json(period).get("execution_domain")=="PAPER"
            and cfg.get("feed_directory","").startswith(
                "/home/alex/.local/state/heptatrader/"),
            "PAPER_SOURCE_NOT_ATTESTED")
    timestamp=now_ms()
    first=((timestamp+MIN_LEAD_MS+STEP_MS-1)//STEP_MS)*STEP_MS
    owner_dir(root,create=True)
    plan={
        "schema":SCHEMA+".plan.v1",
        "registered_at_ms":timestamp,
        "first_cycle_start_ms":first,
        "cycle_schedule_ms":[first+i*STEP_MS for i in range(N)],
        "count":N,"spacing_ms":STEP_MS,
        "maximum_lateness_ms":MAX_LATE_MS,
        "maximum_original_status_age_ms":REPORTING_BUDGET_MS,
        "config_path":str(config_file),
        "config_sha256":sha(config_file),
        "installed_monitor_path":str(installed_source),
        "installed_monitor_sha256":ORIGINAL_MONITOR_SHA,
        "source_module_sha256":FAST_MONITOR_SHA,
        "verified_sealed_cache_sha256":VERIFIED_CACHE_SHA,
        "runner_code_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_archive_code_pins":archive_code_pins(),
        "period_sha256":cfg["period_sha256"],
        "original_feed_dir":cfg["feed_directory"],
        "exact_original_monitor_rule":RULE,
        "cache_warm_reuse_requires_full_source_sha256":True,
        "cache_unsealed_segments_must_be_reparsed":True,
        "production_monitor_modified":False,
        "production_cutover_authorized":False,
        "model_training_or_actor_update":False,
        "original_BTC_FX_driver_changes":False,
        "retrospective_cohort_backfilled":False,
        "broker_quotes_independently_signed":False,
        "product_owner_or_witness_enrolled":False,
        "trade_authority":False,"broker_queries_or_orders_sent":0,
    }
    write_once(root/"plan.json",canonical(plan)+b"\n")
    return {"state":"FUTURE_480_VERIFIED_CACHE_HEALTH_PRESEALED",
            "plan_sha256":sha(root/"plan.json"),
            "first_cycle_utc":__import__("datetime").datetime.fromtimestamp(
                first/1000,__import__("datetime").timezone.utc).isoformat(),
            "first_cycle_start_ms":first,
            "count":N,"trading_authority":False}

def verify_plan(root):
    root=owner_dir(root)
    verify_candidate()
    plan=strict(private_source(root/"plan.json",32768))
    require(plan.get("schema")==SCHEMA+".plan.v1"
            and plan.get("runner_code_sha256")==hashlib.sha256(
                Path(__file__).read_bytes()).hexdigest()
            and plan.get("source_archive_code_pins")==archive_code_pins()
            and plan.get("source_module_sha256")==FAST_MONITOR_SHA
            and plan.get("verified_sealed_cache_sha256")==VERIFIED_CACHE_SHA
            and plan.get("installed_monitor_sha256")==ORIGINAL_MONITOR_SHA
            and plan.get("count")==N and plan.get("spacing_ms")==STEP_MS
            and plan.get("maximum_lateness_ms")==MAX_LATE_MS
            and plan.get("maximum_original_status_age_ms")==REPORTING_BUDGET_MS
            and plan.get("registered_at_ms")+MIN_LEAD_MS<=plan["first_cycle_start_ms"]
            and plan.get("cycle_schedule_ms")==[
                plan["first_cycle_start_ms"]+i*STEP_MS for i in range(N)]
            and plan.get("exact_original_monitor_rule")==RULE
            and plan.get("production_monitor_modified") is False
            and plan.get("production_cutover_authorized") is False
            and plan.get("model_training_or_actor_update") is False
            and plan.get("retrospective_cohort_backfilled") is False
            and plan.get("trade_authority") is False
            and plan.get("broker_queries_or_orders_sent")==0,
            "CACHE480_FROZEN_PLAN_CHANGED")
    require(sha(plan["config_path"])==plan["config_sha256"],
            "ORIGINAL_SOURCE_CONFIG_CHANGED")
    original_observer(plan["installed_monitor_path"])
    cfg=read_json(Path(plan["config_path"]))
    require(cfg.get("period_sha256")==plan["period_sha256"]
            and cfg.get("feed_directory")==plan["original_feed_dir"]
            and sha(cfg["period_path"])==plan["period_sha256"]
            and cfg.get("automatic_trading") is False,
            "ORIGINAL_PAPER_PERIOD_CHANGED")
    return root,plan,cfg

def review_health(status,plan,started,published):
    # No diagnostic can transform original HOLD into READY.
    issues=verify_status(status,plan["period_sha256"],started)
    if (type(status.get("at_ms")) is not int or
            not 0<=published-status["at_ms"]<=REPORTING_BUDGET_MS):
        issues.append("ORIGINAL_STATUS_STALE_AT_PUBLICATION")
    return sorted(set(issues))

def run(root,*, assessment=assess, cache_type=VerifiedSealedReadinessCache):
    root,plan,cfg=verify_plan(root)
    require(now_ms()<plan["first_cycle_start_ms"],
            "ORIGINAL_480_COHORT_STARTED_TOO_LATE")
    write_once(root/"worker-start.json",canonical({
        "schema":SCHEMA+".worker.v1","started_at_ms":now_ms(),
        "plan_sha256":sha(root/"plan.json"),"no_restart_no_retry":True,
        "no_production_service_mutation":True,"broker_orders_sent":0,
    })+b"\n")
    from sandbox import enter
    sandbox=enter({
        "output_dir":str(root),
        "period_path":cfg["period_path"],
        "legacy_capture":cfg["seed_dataset"],
        "oms_path":cfg["oms_path"],
        "export_dir":cfg["export_directory"],
        "read_only_paths":[cfg["feed_directory"],
                           cfg["runtime_directory"],plan["config_path"]]
    })
    write_once(root/"sandbox.json",canonical(sandbox)+b"\n")
    cache=cache_type()
    prior="0"*64
    retained=[]
    max_age_at_pub=0
    for index,target in enumerate(plan["cycle_schedule_ms"]):
        wait_to(target)
        started=now_ms()
        require(not (root/("cycle-%03d-receipt.json"%index)).exists(),
                "DUPLICATE_OBSERVATION_NO_RETRY")
        status_name="cycle-%03d-status.json"%index
        if started>target+MAX_LATE_MS:
            outcome={"state":"CENSORED_SCHEDULE_MISSED",
                     "issues":["ORIGINAL_FIXED_SCHEDULE_MISSED"],
                     "status_sha256":None,"original_asof_ms":None,
                     "source_ready":False,"publisher_eligible":False}
        else:
            try:
                status=assessment(cfg,quote_loader=cache.read_quotes)
                # Publication means the ORIGINAL bytes have actually been
                # fsynced. Do not claim an earlier, optimistic timestamp.
                status_sha=write_once(root/status_name,canonical(status)+b"\n")
                published=now_ms()
                issues=review_health(status,plan,started,published)
                state="CAPTURED_ORIGINAL_CACHE_HEALTH" if not issues else "HOLD_UNQUALIFIED_SOURCE"
                age=(published-status.get("at_ms")
                     if type(status.get("at_ms")) is int else None)
                if type(age) is int:max_age_at_pub=max(max_age_at_pub,age)
                outcome={
                    "state":state,"issues":issues,"status_sha256":status_sha,
                    "original_asof_ms":status.get("at_ms"),"published_at_ms":published,
                    # A malformed/late purported READY is NOT an eligible
                    # operational source status; preserve its raw state.
                    "source_ready":not issues and status.get("state")==
                        "READY_FOR_REVIEW_NOT_AUTHORIZATION",
                    "original_reported_state":status.get("state"),
                    "source_usable_probes":status.get("usable"),
                    "original_monitor_source_prefix_digest":
                        status.get("source_prefix_digest"),
                    "publisher_eligible":not issues,
                }
            except Exception as exc:
                outcome={"state":"HOLD_SOURCE_ASSESSMENT_FAILURE",
                         "issues":["SOURCE_FAILURE_"+type(exc).__name__],
                         "status_sha256":None,"original_asof_ms":None,
                         "source_ready":False,"publisher_eligible":False}
        outcome.update({
            "schema":SCHEMA+".cycle.v1","index":index,
            "fixed_target_ms":target,
            "actual_assessment_started_ms":started,
            "record_sealed_at_ms":now_ms(),
            "previous_receipt_sha256":prior,
            "period_sha256":plan["period_sha256"],
            "trade_authority":False,"broker_orders_sent":0
        })
        previous=write_once(root/("cycle-%03d-receipt.json"%index),
                            canonical(outcome)+b"\n")
        prior=previous
        retained.append({"index":index,"state":outcome["state"],
                         "original_source_ready":outcome["source_ready"],
                         "receipt_sha256":previous})
        if index%16==0:
            print(json.dumps({"cycle":index,"state":outcome["state"],
                "source_ready":outcome["source_ready"],
                "original_evidence_sha256":previous,
                "trade_authority":False}),flush=True)
    stats=cache.counters()
    completed={
        "schema":SCHEMA+".final.v1","plan_sha256":sha(root/"plan.json"),
        "worker_receipt_chain_head":prior,
        "completed_at_ms":now_ms(),"count":N,
        "windows":retained,
        "full_source_status_count":sum(
            (root/("cycle-%03d-status.json"%i)).exists() for i in range(N)),
        "ready_count":sum(x["original_source_ready"] for x in retained),
        "non_ready_count":sum(not x["original_source_ready"] for x in retained),
        "max_source_age_at_publication_ms":max_age_at_pub,
        "verified_cache_counters":stats,
        "production_monitor_mutation":False,
        "native_training_or_trading_approved":False,
        "broker_orders_sent":0,"trade_authority":False,
    }
    write_once(root/"final.json",canonical(completed)+b"\n")
    return {"state":"VERIFIED_CACHE_480_NATURALLY_COMPLETE_RESEARCH_ONLY",
            "full_source_status_count":completed["full_source_status_count"],
            "original_ready":completed["ready_count"],
            "original_hold":completed["non_ready_count"],
            "max_source_age_at_publication_ms":max_age_at_pub,
            "orders_sent":0}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--mode",required=True,choices=("preseal","verify","run"))
    p.add_argument("--root",required=True,type=Path)
    p.add_argument("--config",type=Path)
    p.add_argument("--installed-source",type=Path)
    args=p.parse_args()
    os.umask(0o077)
    if args.mode=="preseal":
        require(args.config is not None and args.installed_source is not None,
                "MISSING_PRESEAL_SOURCES")
        out=preseal(root=args.root,config_file=args.config,
                    installed_source=args.installed_source)
    elif args.mode=="verify":
        _,pl,_=verify_plan(args.root)
        out={"state":"FROZEN_REAL_ORIGINAL_CACHE_PLAN_VALID",
             "full_cycles":len(pl["cycle_schedule_ms"]),
             "trade_authority":False}
    else:
        out=run(args.root)
    print(json.dumps(out,sort_keys=True),flush=True)
if __name__=="__main__":main()
