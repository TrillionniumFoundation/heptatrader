"""Archive every complete original-PAPER source health evaluation in a bounded sandbox.

For source observability only; not a product source or decision/actor grant.
Does not modify the installed health service or old frozen research windows.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import stat
import time

from laya_cell.integrity import read_json
from laya_cell.source_readiness import assess, RULE
from historical_training.common import sha

SCHEMA="heptatrader.fx-original-fullcycle-source-archive.v1"
N=24
STEP_MS=15000
MIN_LEAD_MS=60000
MAX_SOURCE_AGE_MS=30000
MAX_LATE_MS=3000
STATES={"READY_FOR_REVIEW_NOT_AUTHORIZATION","HOLD_NEW_FUSION_WINDOW"}
class AuditHold(ValueError): pass

def require(ok, reason):
    if not ok: raise AuditHold(reason)
def now_ms():
    return time.time_ns()//1000000
def digest(raw):
    return hashlib.sha256(raw).hexdigest()
def canonical(o):
    return json.dumps(o,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
def code_sha():
    return digest(Path(__file__).read_bytes())
def owner_dir(p, create=False):
    p=Path(p)
    require(p.is_absolute() and ".." not in p.parts and not p.is_symlink(),"ARCHIVE_DIR_INVALID")
    if create:
        require(not p.exists(),"ARCHIVE_DIR_ALREADY_EXISTS")
        p.mkdir(parents=True,mode=0o700)
    st=p.stat()
    require(stat.S_ISDIR(st.st_mode) and st.st_uid==os.geteuid()
            and stat.S_IMODE(st.st_mode)==0o700, "ARCHIVE_DIR_NOT_OWNER_ONLY")
    return p
def write_once(path, data):
    path=Path(path)
    owner_dir(path.parent)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(data);f.flush();os.fsync(f.fileno())
    dfd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(dfd)
    finally:os.close(dfd)
    return digest(data)
def private_source(path,max_bytes=512*1024):
    p=Path(path)
    require(p.is_absolute() and not p.is_symlink(),"ARCHIVE_SOURCE_PATH")
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        s=os.fstat(fd)
        require(stat.S_ISREG(s.st_mode) and s.st_uid==os.geteuid()
            and s.st_nlink==1 and not s.st_mode&0o077
            and 0<s.st_size<=max_bytes,"ARCHIVE_SOURCE_INVALID")
        chunks=[]
        size=0
        while size<s.st_size:
            b=os.read(fd,min(131072,s.st_size-size))
            require(bool(b),"ARCHIVE_SHORT_READ")
            chunks.append(b);size+=len(b)
        e=os.fstat(fd)
        require((s.st_ino,s.st_dev,s.st_size,s.st_mtime_ns)==
                (e.st_ino,e.st_dev,e.st_size,e.st_mtime_ns),
                "ARCHIVE_SOURCE_MUTATED_DURING_READ")
        return b"".join(chunks)
    finally:os.close(fd)
def strict(raw):
    def pairs(items):
        d={}
        for k,v in items:
            require(k not in d,"DUPLICATE_JSON_KEY")
            d[k]=v
        return d
    return json.loads(raw,object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(AuditHold("NONFINITE_JSON")))

def verify_status(status, expected_period, assumed_start_ms):
    issues=[]
    if status.get("schema")!="heptatrader.quote-readiness-status.v1":
        issues.append("HEALTH_SCHEMA_CHANGED")
    if status.get("period_sha256")!=expected_period:
        issues.append("HEALTH_PERIOD_CHANGED")
    if status.get("rule")!=RULE:
        issues.append("HEALTH_RULE_CHANGED")
    if status.get("state") not in STATES:
        issues.append("UNKNOWN_HEALTH_STATE")
    count=status.get("counts")
    probes=status.get("probes")
    if not (type(probes) is list and len(probes)==24
            and all(type(v) is dict and type(v.get("status")) is str for v in probes)
            and type(count) is dict):
        issues.append("PROBES_NOT_COMPLETE")
    else:
        actual=dict(Counter(v["status"] for v in probes))
        if (actual!=count or status.get("usable")!=actual.get("USABLE",0)
                or status.get("unusable")!=24-actual.get("USABLE",0)):
            issues.append("PROBE_COUNTS_CHANGED")
    prefixes=status.get("source_prefixes")
    if (type(prefixes) is not list
            or digest(canonical(prefixes))!=status.get("source_prefix_digest")):
        issues.append("PREFIXES_SHA_CHANGED")
    stamp=status.get("at_ms")
    if type(stamp) is not int or not assumed_start_ms<=stamp<=assumed_start_ms+3000:
        issues.append("SOURCE_ASOF_NOT_ACTUAL_EVALUATION_TIME")
    if (status.get("automatic_trial_registration") is not False
            or status.get("trading_authority") is not False
            or status.get("orders_sent")!=0):
        issues.append("SOURCE_MONITOR_AUTHORITY_CHANGED")
    age=status.get("latest_quote_age_ms")
    last=status.get("latest_source_quote_ms")
    if (type(age) is not int or type(last) is not int
            or type(stamp) is not int or stamp-last!=age):
        issues.append("SOURCE_AGE_CLOCK_MISMATCH")
    if status.get("state")=="READY_FOR_REVIEW_NOT_AUTHORIZATION":
        if (status.get("reasons")!=[]
                or type(age) is not int or not 0<=age<=RULE["source_ttl_ms"]
                or status.get("usable",0)<RULE["minimum_usable"]
                or status.get("unusable",24)>RULE["maximum_missing"]):
            issues.append("READY_CLAIM_WITH_UNQUALIFIED_IB_SOURCE")
    return sorted(set(issues))

def preseal(root,config_path,installed_path):
    root=Path(root)
    config_path,installed_path=Path(config_path),Path(installed_path)
    cfg=read_json(config_path)
    period=Path(cfg["period_path"])
    require(cfg.get("schema")=="heptatrader.laya-resident-config.v1"
            and cfg.get("automatic_trading") is False
            and cfg.get("period_sha256")==sha(period)
            and read_json(period).get("execution_domain")=="PAPER",
            "PAPER_SOURCE_SCOPE_NOT_PINNED")
    require(installed_path.is_file() and not installed_path.is_symlink()
            and cfg["feed_directory"].startswith("/home/alex/.local/state/heptatrader/"),
            "PRODUCTION_HEALTH_PROVENANCE_INVALID")
    now=now_ms()
    start=((now+MIN_LEAD_MS+STEP_MS-1)//STEP_MS)*STEP_MS
    plan={
        "schema":SCHEMA+".plan.v1",
        "registered_at_ms":now,
        "first_cycle_start_ms":start,
        "cycle_schedule_ms":[start+i*STEP_MS for i in range(N)],
        "full_snapshots_count":N,
        "candidate_assessment_cadence_ms":STEP_MS,
        "config_path":str(config_path),
        "config_sha256":sha(config_path),
        "installed_prod_source_path":str(installed_path),
        "installed_prod_source_sha256":sha(installed_path),
        "sandbox_candidate_source_sha256":sha(Path(__file__).parent.parent/"neuron_learning"/"laya_cell"/"source_readiness.py"),
        "cohort_runner_sha256":code_sha(),
        "period_sha256":cfg["period_sha256"],
        "original_feed_dir":cfg["feed_directory"],
        "original_monitor_rules":RULE,
        "maximum_status_age_ms":MAX_SOURCE_AGE_MS,
        "maximum_schedule_delay_ms":MAX_LATE_MS,
        "no_production_monitor_cutover":True,
        "no_training_or_trading":True,
        "outcome_or_model_prediction_count":0,
        "ib_source_broker_signed":False,
        "product_owner_or_witness_enrolled":False,
        "broker_requests_or_orders_sent":0,
        "trade_authority":False,
    }
    owner_dir(root,create=True)
    write_once(root/"plan.json",canonical(plan)+b"\n")
    return {"state":"FUTURE_SOURCE_FULL_SNAPSHOTS_PRESEALED",
            "first_cycle_start_ms":start,"plan_sha256":sha(root/"plan.json"),
            "full_snapshots_expected":N,"no_trading":True}

def verify_plan(root):
    root=owner_dir(root)
    plan=strict(private_source(root/"plan.json",16000))
    require(plan.get("schema")==SCHEMA+".plan.v1"
            and plan.get("cohort_runner_sha256")==code_sha()
            and plan.get("full_snapshots_count")==N
            and plan.get("candidate_assessment_cadence_ms")==STEP_MS
            and plan.get("cycle_schedule_ms")==[
                plan["first_cycle_start_ms"]+i*STEP_MS for i in range(N)]
            and plan.get("registered_at_ms")+MIN_LEAD_MS<=plan["first_cycle_start_ms"]
            and plan.get("maximum_status_age_ms")==MAX_SOURCE_AGE_MS
            and plan.get("maximum_schedule_delay_ms")==MAX_LATE_MS
            and plan.get("no_production_monitor_cutover") is True
            and plan.get("no_training_or_trading") is True
            and plan.get("trade_authority") is False
            and plan.get("broker_requests_or_orders_sent")==0,
            "FROZEN_SOURCE_PLAN_INVALID")
    cfg=read_json(Path(plan["config_path"]))
    require(sha(plan["config_path"])==plan["config_sha256"]
            and sha(plan["installed_prod_source_path"])==plan["installed_prod_source_sha256"]
            and sha(Path(__file__).parent.parent/"neuron_learning"/"laya_cell"/"source_readiness.py")==plan["sandbox_candidate_source_sha256"]
            and cfg.get("period_sha256")==plan["period_sha256"]
            and cfg.get("feed_directory")==plan["original_feed_dir"]
            and read_json(Path(cfg["period_path"])).get("execution_domain")=="PAPER",
            "PINNED_PAPER_OR_PRODUCER_CHANGED")
    return root,plan,cfg

def wait_to(target):
    while now_ms()<target:
        ms=target-now_ms()
        time.sleep(min(2.,max(.003,(ms-50)/1000)))

def run(root):
    root,plan,cfg=verify_plan(root)
    require(now_ms()<plan["first_cycle_start_ms"],"MISSED_FIRST_FUTURE_CYCLE")
    write_once(root/"worker-start.json",canonical({
        "schema":SCHEMA+".worker.v1","start_ms":now_ms(),
        "plan_sha256":sha(root/"plan.json"),"no_restart":True,
        "production_monitor_not_modified":True,"broker_orders_sent":0,
    })+b"\n")
    # Only sandbox has access to the original quote source. No broker socket,
    # trading client, user service edit, or installed monitor mutation.
    from sandbox import enter
    receipt=enter({
        "output_dir":str(root),
        "period_path":cfg["period_path"],
        "legacy_capture":cfg["seed_dataset"],
        "oms_path":cfg["oms_path"],
        "export_dir":cfg["export_directory"],
        "read_only_paths":[cfg["feed_directory"],cfg["runtime_directory"],plan["config_path"]],
    })
    write_once(root/"sandbox.json",canonical(receipt)+b"\n")
    previous="0"*64
    records=[]
    for i,target in enumerate(plan["cycle_schedule_ms"]):
        wait_to(target)
        started=now_ms()
        require(not (root/("cycle-%02d-status.json"%i)).exists(),"CYCLE_ALREADY_OBSERVED_NO_RETRY")
        if started>target+MAX_LATE_MS:
            record={"state":"CENSORED_MISSED_FROZEN_CYCLE","issues":["MISSED_FROZEN_SCHEDULE"],
                    "status_sha256":None,"original_asof_ms":None,
                    "publisher_eligible":False,"source_ready":False}
        else:
            try:
                result=assess(cfg)
                payload=canonical(result)+b"\n"
                status_sha=write_once(root/("cycle-%02d-status.json"%i),payload)
                published=now_ms()
                issues=verify_status(result,plan["period_sha256"],started)
                asof=result.get("at_ms")
                if type(asof) is not int or not 0<=published-asof<=MAX_SOURCE_AGE_MS:
                    issues.append("STATUS_STALE_AT_PUBLICATION")
                state="CAPTURED_ORIGINAL_HEALTH" if not issues else "HOLD_UNQUALIFIED_SNAPSHOT"
                record={
                    "state":state,"issues":sorted(set(issues)),
                    "status_sha256":status_sha,"original_asof_ms":asof,
                    "source_ready":result.get("state")=="READY_FOR_REVIEW_NOT_AUTHORIZATION",
                    "source_usable_probes":result.get("usable"),
                    "source_hold_reasons":result.get("reasons"),
                    "published_at_ms":published,
                    "source_prefix_digest":result.get("source_prefix_digest"),
                    "publisher_eligible":not issues,
                }
            except Exception as exc:
                record={"state":"HOLD_SOURCE_ASSESSMENT_FAILURE",
                        "issues":["SOURCE_FAILURE_"+type(exc).__name__],
                        "status_sha256":None,"original_asof_ms":None,
                        "publisher_eligible":False,"source_ready":False}
        record.update({
            "schema":SCHEMA+".cycle.v1","index":i,
            "frozen_target_ms":target,"assessment_started_ms":started,
            "record_sealed_at_ms":now_ms(),
            "previous_receipt_sha256":previous,
            "original_period_sha256":plan["period_sha256"],
            "trade_authority":False,"orders_sent":0,
        })
        this=write_once(root/("cycle-%02d-receipt.json"%i),canonical(record)+b"\n")
        previous=this
        records.append({"index":i,"status":record["state"],
                        "receipt_sha256":this,"source_ready":record["source_ready"]})
        print(json.dumps({"cycle":i,"state":record["state"],
                          "source_ready":record["source_ready"],"broker_orders_sent":0}),flush=True)
    complete={
        "schema":SCHEMA+".final.v1",
        "plan_sha256":sha(root/"plan.json"),
        "receipt_chain_head":previous,
        "cycles":records,
        "full_snapshots_written":sum(
            (root/("cycle-%02d-status.json"%i)).exists() for i in range(N)),
        "no_prod_monitor_cutover":True,
        "production_trading_grant":False,"broker_orders_sent":0,
    }
    write_once(root/"final.json",canonical(complete)+b"\n")
    return {"state":"FINITE_ORIGINAL_HEALTH_FULLCYCLE_RESEARCH_COMPLETE",
            "snapshots":complete["full_snapshots_written"],"broker_orders_sent":0}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--mode",required=True,choices=("preseal","verify","run"))
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--config",type=Path)
    p.add_argument("--installed-source",type=Path)
    args=p.parse_args()
    os.umask(0o077)
    if args.mode=="preseal":
        require(args.config and args.installed_source,"MISSING_PRESEAL_ROOT")
        result=preseal(args.root,args.config,args.installed_source)
    elif args.mode=="verify":
        _,pl,_=verify_plan(args.root)
        result={"state":"FULLCYCLE_RESEARCH_PLAN_CODE_AND_PERIOD_MATCH",
                "expected_cycles":len(pl["cycle_schedule_ms"]),"no_trading":True}
    else:result=run(args.root)
    print(json.dumps(result,sort_keys=True),flush=True)
if __name__=="__main__":main()
