"""Independent read-only closeout for activated Laya archive-aware Guard.

The stage/activation receipt by itself is not evidence that a worker trained,
that a new candidate is genuine or that original failure history was retained.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

from historical_training.common import EvidenceError, read, sha, digest
from historical_training.laya_guard_archive_activate import (
    STAGED, CANARY, OUTPUT, EXPECTED, CONF, ROLE_UNIT,
    demand, private_existing, systemctl, write_once,
)

ROOT=Path.home()/".local/state/heptatrader/neuron-learning/laya-resident-reset-1791349390533"
RESEARCH_GUARD=Path.home()/".local/state/heptatrader/neuron-learning/resident-resource-guard-20261008"
JOB_ID="3e1d1698a4c94a97e6accb95808d2eb96d583424af863df8fd9ddd5e5b25a311"
CANDIDATE="1797b31505c498fa320e910a80426bccb083cf1ceeb9da8d7ad54ec9ad692124"

def examine():
    activated=read(private_existing(OUTPUT/"result.json"))
    prereq=read(private_existing(OUTPUT/"preflight.json"))
    demand(activated.get("state")=="GUARDED_RESEARCH_SERVICE_CUTOVER_APPLIED"
           and activated.get("preflight_sha256")==sha(OUTPUT/"preflight.json")
           and activated.get("broker_orders_sent")==0
           and activated.get("canonical_owner_granted") is False
           and activated.get("trading_services_restarted") is False,
           "RESEARCH_ACTIVATION_SOURCE_NOT_PROVEN")
    demand(activated["original_canary_sha256"]==sha(CANARY/"comparison.json")
           and prereq["protected_cache_canary"]["comparison_sha256"]==
             activated["original_canary_sha256"],
           "RESEARCH_PROTECTED_FX_ORIGINAL_CANARY_CHANGED")
    guard=read(private_existing(STAGED/"guard.json"))
    candidate_config=read(private_existing(STAGED/"resident.json"))
    demand(sha(STAGED/"guard.json")==activated["new_config_sha256"]
           and guard["original_config_sha256"]==sha(STAGED/"resident.json")
           and candidate_config.get("automatic_trading") is False
           and candidate_config.get("canonical_owner_endpoint") is None,
           "RESEARCH_RUNTIME_POLICY_CHANGED")
    service_status={}
    for role,unit in ROLE_UNIT.items():
        source=Path.home()/".config/systemd/user"/(unit+".d")/CONF
        demand(source.is_file() and source.is_symlink() is False
               and sha(source)==EXPECTED[role]==activated["installed_dropin_sha256"][role],
               "RESEARCH_CURRENT_UNIT_OVERRIDE_CHANGED:"+role)
        show=systemctl("show",unit,"-p","ActiveState","-p","MainPID",
                       "-p","ExecStart","-p","DropInPaths")
        demand("ActiveState=active" in show
               and str(STAGED/"guard.json") in show
               and CONF in show,
               "RESEARCH_EFFECTIVE_UNIT_CHANGED:"+role)
        fields={}
        for line in show.splitlines():
            if "=" in line:
                key,val=line.split("=",1)
                fields[key]=val
        pid=fields.get("MainPID")
        demand(isinstance(pid,str) and pid.isdigit() and int(pid)>0,
               "RESEARCH_ACTIVATED_PROCESS_NOT_LIVE:"+role)
        service_status[role]={"active":True,"MainPID":int(pid),
                              "dropin_sha256":sha(source),
                              "new_guard_sha256":sha(STAGED/"guard.json")}
    control=read(private_existing(ROOT/"control/status.json"))
    jobs=control.get("job_counts",{})
    demand(jobs.get("COMPLETED",0)>=31
           and jobs.get("FAILED")==1
           and jobs.get("RUNNING")==0
           and jobs.get("ADMITTED")==0
           and control.get("automatic_trading") is False
           and control.get("canonical_agentd_owner_connected") is False
           and control.get("broker_orders_sent")==0,
           "RESEARCH_TRAINER_JOB_OR_AUTHORITY_FRONTIER_CHANGED")
    error_receipts={}
    for role in ROLE_UNIT:
        old=read(private_existing(RESEARCH_GUARD/(role+"-error.json")))
        demand(old.get("active") is False
               and old.get("error")=="RESIDENT_SEGMENT_BOUND_NO_HISTORY_DELETION"
               and type(old.get("resolved_at_ms")) is int,
               "RESEARCH_LEGACY_ARCHIVE_ERROR_NOT_RESOLVED:"+role)
        error_receipts[role]={
            "historical_reason_retained":old["error"],
            "active":False,
            "resolved_at_ms":old["resolved_at_ms"],
        }
    job=read(private_existing(ROOT/"control/jobs"/(JOB_ID+".json")))
    artifact=Path(job["output_directory"])
    demand(artifact==ROOT/"candidates"/JOB_ID/"artifact"
           and job["runtime_directory"]==candidate_config["runtime_directory"]
           and job.get("automatic_trading") is False,
           "RESEARCH_FIRST_NEW_JOB_SOURCE_CHANGED")
    a=read(private_existing(artifact/"candidate.json"),limit=1024*1024)
    demand(a.get("schema")=="heptatrader.laya-resident-candidate.v1"
           and a.get("candidate_id")==CANDIDATE
           and a.get("candidate_id")==digest({k:v for k,v in a.items() if k!="candidate_id"})
           and a.get("adapter_sha256")==sha(artifact/"adapter.safetensors")
           and a.get("status")=="REQUIRES_INDEPENDENT_FUTURE_EVALUATION"
           and a.get("broker_orders_sent")==0
           and a.get("automatic_cutover") is False
           and a.get("canonical_owner_activation_claimed") is False,
           "RESEARCH_FIRST_CANDIDATE_NOT_IMMUTABLE_OR_SAFE")
    log=private_existing(ROOT/"control/logs"/(JOB_ID+".log"))
    events=[]
    for line in log.read_text().splitlines():
        if line.startswith('{"step":'):
            record=json.loads(line)
            if record.get("applied") is True:
                events.append(record["step"])
    demand(events==list(range(1,17)),"RESEARCH_GPU_16_STEPS_NOT_PROVEN")

    evaluation=read(private_existing(ROOT/"evaluation/status.json"))
    demand(evaluation.get("promotion_performed") is False
           and evaluation.get("broker_orders_sent")==0
           and evaluation.get("counts",{}).get("REJECTED",0)>=30
           and evaluation.get("automatic_trading") is False
           and evaluation.get("canonical_owner_connected") is False,
           "RESEARCH_INDEPENDENT_EVALUATOR_NOT_SAFE")
    # The original strategy remains independently active. Its PAPER PnL may
    # change; this read-only audit must not invent account-complete net return.
    pnl=read(private_existing(
        Path.home()/".local/state/heptatrader/pnl/snapshot.json"))
    demand(pnl.get("schema") is not None
           and pnl.get("totals",{}).get("currency")=="USD",
           "RESEARCH_PNL_SNAPSHOT_WRONG_SCOPE")
    receipt={
        "schema":"heptatrader.laya-archive-guard-postactivation-audit.v1",
        "at_ms":time.time_ns()//1000000,
        "activation_result_sha256":sha(OUTPUT/"result.json"),
        "activation_preflight_sha256":sha(OUTPUT/"preflight.json"),
        "protected_canary_sha256":sha(CANARY/"comparison.json"),
        "source_code_sha256":{
            "activation":sha(Path(__file__).with_name("laya_guard_archive_activate.py")),
            "audit":sha(Path(__file__)),
        },
        "actual_systemd_research_units":service_status,
        "new_runtime_directory":candidate_config["runtime_directory"],
        "original_completed_before_activation":30,
        "completed_after_activation":jobs["COMPLETED"],
        "failed_before_and_after":jobs["FAILED"],
        "first_actual_candidate_id":CANDIDATE,
        "first_candidate_manifest_sha256":sha(artifact/"candidate.json"),
        "first_candidate_model_sha256":sha(artifact/"adapter.safetensors"),
        "first_gpu_steps_applied":len(events),
        "first_gpu_job_log_sha256":sha(log),
        "original_training_errors_preserved_but_inactive":error_receipts,
        "independent_evaluator_rejected_plans":evaluation["counts"]["REJECTED"],
        "candidate_requires_independent_future_evaluation":True,
        "canonical_agentd_owner_enrolled":False,
        "training_effectiveness_proven":False,
        "automatic_promotion":False,
        "live_strategy_pnl_usd":pnl["totals"].get("net_total_pnl"),
        "full_account_pnl_qualified":pnl.get("account_complete"),
        "research_orders_sent":0,
        "trading_services_restarted_by_cutover":False,
        "trade_authority":False,
    }
    return receipt

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    os.umask(0o077)
    report=examine()
    path=args.output
    demand(path.parent==OUTPUT,"AUDIT_OUTPUT_OUTSIDE_ACTIVATION_EVIDENCE")
    write_once(path,json.dumps(report,sort_keys=True,
                separators=(",",":")).encode()+b"\n")
    print(json.dumps({
        "schema":report["schema"],
        "trained_candidate":report["first_actual_candidate_id"],
        "steps":report["first_gpu_steps_applied"],
        "completed":report["completed_after_activation"],
        "evaluator_rejected_plans":report["independent_evaluator_rejected_plans"],
        "canonical_owner":False,
        "broker_orders":0,
        "sha256":sha(path)},sort_keys=True))

if __name__=="__main__":
    main()
