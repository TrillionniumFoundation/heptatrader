"""Finite verified-cache prewarm / long sustained 480-cycle health audit.

A NEW prospective 480-cycle study with strict pre-start cache priming, distinct
from the first failing 480-cycle and successful 12-cycle pilot. No client for the broker/driver, no model,
no trading authorization, no production source monitor hot-reload.

Prewarm the verified cache before the first fixed status slot. A cold-start
overrun is evidence of failure, never an excuse to shift the time grid.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import time

from historical_training.common import read, sha
from laya_cell.integrity import read_json
from laya_cell.source_readiness import RULE, assess
from laya_cell.verified_sealed_readiness_cache import VerifiedSealedReadinessCache
from .fx_verified_cache_480 import (
    original_observer, verify_candidate, review_health
)
from .fx_original_health_sustained import (
    owner_dir, write_once, private_source, strict, require, canonical,
    code_pins as archive_code_pins,
)

SCHEMA="heptatrader.fx-cache-prewarm-480-original-health.v1"
N=480
STEP_MS=15_000
MIN_LEAD_MS=120_000
LATEST_START_MS=3_000
MAX_STATUS_AGE_MS=30_000
PREWARM_DONE_LEAD_MS=15_000
MAX_PREWARM_MS=60_000
ORIGINAL_480_FINAL_SHA="8ac37d9c202505e00bcc90daf7ede67dabdc7167eafef526ee17457cc1745353"
PILOT_12_FINAL_SHA="8cc5b7c5d6f44ff01b28b4abc0042c3a11d8ae440fd7ef4639a3a11224a6a0dd"
ORIGINAL_480_FINAL=Path("/home/alex/.local/state/heptatrader/operator-evidence/fx-cache480-25pct-20261013-v1/final.json")
PILOT_12_FINAL=Path("/home/alex/.local/state/heptatrader/operator-evidence/fx-source-cache-prewarm-20261014-v2/final.json")

def require_prior_negative_and_pilot():
    """Baseline failures and positive 12-cycle pilot never silently change."""
    require(sha(ORIGINAL_480_FINAL)==ORIGINAL_480_FINAL_SHA,
            "PRESERVED_ORIGINAL_480_FAILURE_CHANGED")
    require(sha(PILOT_12_FINAL)==PILOT_12_FINAL_SHA,
            "PRESERVED_12_CYCLE_PILOT_CHANGED")
    failed=read(ORIGINAL_480_FINAL, 262144)
    pilot=read(PILOT_12_FINAL, 65536)
    require(failed.get("count")==480 or failed.get("full_snapshots_count")==480,
            "ORIGINAL_480_POPULATION_CHANGED")
    require(pilot.get("count")==12 and pilot.get("operational_ready_count")==11
            and pilot.get("valid_original_status_count")==12
            and pilot.get("production_monitor_cutover") is False,
            "PILOT_RESULTS_CHANGED")
    return True

def now_ms():
    return time.time_ns()//1_000_000

def code_sha():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

def fixed_grid(at_ms):
    require(type(at_ms) is int and at_ms>0,"INVALID_PREWARM_CLOCK")
    first=((at_ms+MIN_LEAD_MS+STEP_MS-1)//STEP_MS)*STEP_MS
    return [first+i*STEP_MS for i in range(N)]

def preseal(*,root,config_path,installed_monitor):
    root=Path(root)
    config_path=Path(config_path)
    verify_candidate()
    require_prior_negative_and_pilot()
    original_observer(installed_monitor)
    cfg=read_json(config_path)
    period=Path(cfg["period_path"])
    require(cfg.get("schema")=="heptatrader.laya-resident-config.v1"
            and cfg.get("automatic_trading") is False
            and cfg.get("period_sha256")==sha(period)
            and read_json(period).get("execution_domain")=="PAPER"
            and cfg.get("feed_directory","").startswith(
                "/home/alex/.local/state/heptatrader/"),
            "ORIGINAL_PAPER_PERIOD_NOT_QUALIFIED")
    t=now_ms()
    schedule=fixed_grid(t)
    owner_dir(root,create=True)
    p={
       "schema":SCHEMA+".plan.v1",
       "registered_at_ms":t,
       "cycle_schedule_ms":schedule,
       "count":N,"spacing_ms":STEP_MS,
       "prewarm_must_finish_before_first_ms":schedule[0]-PREWARM_DONE_LEAD_MS,
       "maximum_prepublication_original_age_ms":MAX_STATUS_AGE_MS,
       "maximum_decision_start_lateness_ms":LATEST_START_MS,
       "config_path":str(config_path),
       "config_sha256":sha(config_path),
       "original_monitor_path":str(installed_monitor),
       "original_monitor_sha256":"63bcef1a6d8bc22e3c2fb10d1dcebc1457559538e6a959004e61eeb88c09c320",
       "research_code_sha256":code_sha(),
       "source_archive_code_pins":archive_code_pins(),
       "original_period_sha256":cfg["period_sha256"],
       "original_feed_root":cfg["feed_directory"],
       "source_readiness_rule":RULE,
       "source_cache_must_verify_all_sealed_bytes_every_cycle":True,
       "cold_prewarm_is_not_an_experiment_slot":True,
       "reference_original_480_cycle_study_is_frozen":True,
       "first_original_cache480_missed_cycle_is_preserved":True,
       "original_480_negative_control_sha256":ORIGINAL_480_FINAL_SHA,
       "prior_12_cycle_pilot_sha256":PILOT_12_FINAL_SHA,
       "cutover_production_monitor":False,
       "broker_original_quotes_are_not_digitally_signed":True,
       "automatic_trading":False,"actor_training":False,
       "production_owner_witness_admission":False,
       "trade_authority":False,"broker_queries_sent":0,"broker_orders_sent":0,
    }
    write_once(root/"plan.json",canonical(p)+b"\n")
    return {"state":"FOUR_HUNDRED_EIGHTY_FUTURE_CACHE_WARMUP_SLOTS_PRESEALED",
            "count":N,"first_start_utc":datetime.fromtimestamp(
                schedule[0]/1000,timezone.utc).isoformat(),
            "plan_sha256":sha(root/"plan.json"),
            "broker_orders_sent":0}

def verify_plan(root):
    root=owner_dir(Path(root))
    verify_candidate()
    p=strict(private_source(root/"plan.json",16384))
    require(p.get("schema")==SCHEMA+".plan.v1"
            and p.get("research_code_sha256")==code_sha()
            and p.get("source_archive_code_pins")==archive_code_pins()
            and p.get("source_readiness_rule")==RULE
            and p.get("count")==N and p.get("spacing_ms")==STEP_MS
            and p.get("cycle_schedule_ms")==fixed_grid(p["registered_at_ms"])
            and p.get("prewarm_must_finish_before_first_ms")==
                p["cycle_schedule_ms"][0]-PREWARM_DONE_LEAD_MS
            and p.get("maximum_prepublication_original_age_ms")==MAX_STATUS_AGE_MS
            and p.get("maximum_decision_start_lateness_ms")==LATEST_START_MS
            and p.get("cutover_production_monitor") is False
            and p.get("original_480_negative_control_sha256")==ORIGINAL_480_FINAL_SHA
            and p.get("prior_12_cycle_pilot_sha256")==PILOT_12_FINAL_SHA
            and p.get("trade_authority") is False
            and p.get("actor_training") is False
            and p.get("broker_orders_sent")==0,
            "FROZEN_WARMUP_PROTOCOL_CHANGED")
    c=Path(p["config_path"])
    require(sha(c)==p["config_sha256"]
            and read_json(c)["period_sha256"]==p["original_period_sha256"]
            and read_json(c)["feed_directory"]==p["original_feed_root"],
            "ORIGINAL_SOURCE_CONFIG_CHANGED")
    original_observer(Path(p["original_monitor_path"]))
    return root,p,read_json(c)

def prewarm_health(cfg,cache,*,assessment=assess):
    # Do not publish a prewarm status to the production monitor.
    started=now_ms()
    status=assessment(cfg,quote_loader=cache.read_quotes)
    finished=now_ms()
    require(type(status) is dict,"PREWARM_SOURCE_NOT_DICT")
    issues=review_health(status,{"period_sha256":cfg["period_sha256"]},
                         started,finished)
    return started,finished,status,issues

def frozen_period_scope(plan):
    """Bridge the frozen study's `original_period_sha256` to the monitor
    verification contract; a missing/malformed identity must fail closed."""
    period=plan.get("original_period_sha256")
    require(type(period) is str and len(period)==64 and
            all(c in "0123456789abcdef" for c in period),
            "FROZEN_ORIGINAL_PAPER_PERIOD_NOT_PINNED")
    return {"period_sha256":period}

def decide_cycle(status,plan,started,published):
    issues=review_health(status,plan,started,published)
    ready=(not issues and status.get("state")=="READY_FOR_REVIEW_NOT_AUTHORIZATION")
    require(status.get("state") in ("HOLD_NEW_FUSION_WINDOW",
                                      "READY_FOR_REVIEW_NOT_AUTHORIZATION"),
            "UNEXPECTED_ORIGINAL_MONITOR_STATE")
    return {
        "state":("SOURCE_READY_RESEARCH_ONLY" if ready else
                 "SOURCE_HOLD_RETAINED" if not issues else
                 "SOURCE_UNQUALIFIED_RETAINED"),
        "source_ready":ready,
        "source_state_as_observed":status.get("state"),
        "issues":sorted(set(issues)),
        "status_age_at_publication_ms":(
            published-status["at_ms"] if type(status.get("at_ms")) is int else None),
    }

def wait_to(target):
    while True:
        left=target-now_ms()
        if left<=0:return
        time.sleep(min(3.,max(.001,(left-30)/1000)))

def run(root,*,assessment=assess,cache_type=VerifiedSealedReadinessCache):
    root,plan,cfg=verify_plan(root)
    require(now_ms()<plan["prewarm_must_finish_before_first_ms"],
            "PREWARM_RUNNER_LAUNCHED_TOO_LATE")
    require(not (root/"worker-start.json").exists(),"RESEARCH_WORKER_ALREADY_STARTED")
    write_once(root/"worker-start.json",canonical({
        "schema":SCHEMA+".worker.v1","started_at_ms":now_ms(),
        "plan_sha256":sha(root/"plan.json"),
        "no_production_monitor_change":True,
        "restart_allowed":False,"broker_orders_sent":0
    })+b"\n")
    from sandbox import enter
    sandbox=enter({
        "output_dir":str(root),"period_path":cfg["period_path"],
        "legacy_capture":cfg["seed_dataset"],"oms_path":cfg["oms_path"],
        "export_dir":cfg["export_directory"],
        "read_only_paths":[cfg["feed_directory"],cfg["runtime_directory"],
                           plan["config_path"]]
    })
    write_once(root/"sandbox.json",canonical(sandbox)+b"\n")
    cache=cache_type()
    # REAL first parse is before all 480 fixed slots and always retained.
    prewarm_started=now_ms()
    try:
        s,e,status,issues=prewarm_health(cfg,cache,assessment=assessment)
        source_sha=write_once(root/"prewarm-source.json",canonical(status)+b"\n")
        finish=now_ms()
        overrun=(finish>plan["prewarm_must_finish_before_first_ms"]
                 or e-s>MAX_PREWARM_MS)
        receipt={
            "schema":SCHEMA+".prewarm.v1",
            "started_ms":s,"finished_ms":finish,
            "original_asof_ms":status.get("at_ms"),
            "original_source_state":status.get("state"),
            "source_health_issues":issues,
            "source_bytes_sha256":source_sha,
            "prewarm_within_frozen_before_first_deadline":not overrun,
            "cache_counters":cache.counters(),
            "trade_authority":False,"broker_orders_sent":0,
        }
    except Exception as err:
        receipt={
            "schema":SCHEMA+".prewarm.v1",
            "started_ms":prewarm_started,"finished_ms":now_ms(),
            "original_source_state":None,
            "source_health_issues":["PREWARM_FAILURE_"+type(err).__name__],
            "source_bytes_sha256":None,
            "prewarm_within_frozen_before_first_deadline":False,
            "trade_authority":False,"broker_orders_sent":0,
        }
    prewarm_sha=write_once(root/"prewarm-receipt.json",canonical(receipt)+b"\n")
    if not receipt["prewarm_within_frozen_before_first_deadline"]:
        # Cannot retroactively claim the cache had been warmed in time;
        # no slot can become a successful cache-warm experiment.
        print(json.dumps({"state":"PREWARM_HOLD_NO_COLD_WARM_STATUS",
                          "prewarm_receipt_sha256":prewarm_sha,
                          "broker_orders_sent":0}),flush=True)
    chain="0"*64
    captured=[]
    max_published_age=0
    max_age_before_next=None
    prev_source_asof=None
    for i,target in enumerate(plan["cycle_schedule_ms"]):
        wait_to(target)
        started=now_ms()
        status_name="slot-%02d-original-status.json"%i
        outcome={
            "schema":SCHEMA+".slot.v1","index":i,"target_ms":target,
            "assessment_started_ms":started,"record_sealed_at_ms":None,
            "prewarm_receipt_sha256":prewarm_sha,
            "previous_receipt_sha256":chain,
            "source_status_sha256":None,"source_ready":False,
            "issues":[],"broker_orders_sent":0,"trade_authority":False,
        }
        if not receipt["prewarm_within_frozen_before_first_deadline"]:
            outcome["state"]="CENSORED_PREWARM_NOT_ELIGIBLE"
            outcome["issues"]=["PREWARM_NOT_WITHIN_FROZEN_DEADLINE"]
        elif started>target+LATEST_START_MS:
            outcome["state"]="CENSORED_FIXED_SLOT_MISSED"
            outcome["issues"]=["ORIGINAL_FIXED_SCHEDULE_MISSED"]
        else:
            try:
                status=assessment(cfg,quote_loader=cache.read_quotes)
                status_sha=write_once(root/status_name,canonical(status)+b"\n")
                published=now_ms()
                decision=decide_cycle(status,frozen_period_scope(plan),started,published)
                outcome.update(decision)
                outcome.update({
                    "source_status_sha256":status_sha,
                    "source_original_asof_ms":status.get("at_ms"),
                    "published_ms":published,
                })
                age=outcome["status_age_at_publication_ms"]
                if type(age) is int:
                    max_published_age=max(max_published_age,age)
                if type(prev_source_asof) is int:
                    previous_age=published-prev_source_asof
                    max_age_before_next=max(max_age_before_next or 0,previous_age)
                prev_source_asof=status.get("at_ms")
            except Exception as err:
                outcome["state"]="HOLD_ORIGINAL_SOURCE_READ_FAILURE"
                outcome["issues"]=["ASSESSMENT_"+type(err).__name__]
        outcome["record_sealed_at_ms"]=now_ms()
        chain=write_once(root/("slot-%02d-receipt.json"%i),
                         canonical(outcome)+b"\n")
        captured.append({"index":i,"state":outcome["state"],
                         "source_ready":outcome["source_ready"],
                         "receipt_sha256":chain})
        print(json.dumps({"slot":i,"state":outcome["state"],
                          "original_source_ready":outcome["source_ready"],
                          "orders_sent":0}),flush=True)
    report={
        "schema":SCHEMA+".final.v1",
        "frozen_plan_sha256":sha(root/"plan.json"),
        "prewarm_receipt_sha256":prewarm_sha,
        "cached_counters":cache.counters(),
        "count":N,"slot_results":captured,
        "valid_original_status_count":sum((root/("slot-%02d-original-status.json"%i)).exists()
                                          for i in range(N)),
        "operational_ready_count":sum(x["source_ready"] for x in captured),
        "max_original_asof_to_own_publication_ms":max_published_age,
        "max_original_asof_to_next_actual_publication_ms":max_age_before_next,
        "all_original_HOLD_and_missed_slots_retained":True,
        "production_monitor_cutover":False,
        "old_480_failure_reclassified":False,
        "trade_authority":False,"broker_queries_or_orders_sent":0,
    }
    write_once(root/"final.json",canonical(report)+b"\n")
    return {"state":"FINITE_480_ORIGINAL_PREWARM_RESEARCH_ENDED",
            "count":N,"operational_ready_count":report["operational_ready_count"],
            "max_original_asof_to_next_actual_publication_ms":max_age_before_next,
            "orders_sent":0,"production_monitor_cutover":False}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--mode",required=True,choices=("preseal","verify","run"))
    p.add_argument("--root",required=True,type=Path)
    p.add_argument("--config",type=Path)
    p.add_argument("--installed-source",type=Path)
    a=p.parse_args()
    os.umask(0o077)
    if a.mode=="preseal":
        require(a.config is not None and a.installed_source is not None,
                "PRESEAL_PATH_REQUIRED")
        result=preseal(root=a.root,config_path=a.config,
                       installed_monitor=a.installed_source)
    elif a.mode=="verify":
        r,plan,cfg=verify_plan(a.root)
        result={"state":"IMMUTABLE_RESEARCH_PREWARM_PLAN_VERIFIED",
                "slots":len(plan["cycle_schedule_ms"]),"trade_authority":False}
    else:result=run(a.root)
    print(json.dumps(result,sort_keys=True),flush=True)
if __name__=="__main__":main()
